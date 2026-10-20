import { useState, useEffect, useRef } from 'react';
import Editor, { type OnMount, type BeforeMount } from '@monaco-editor/react';
import { strToU8, zipSync } from 'fflate';
import { Play, Database, Square, Download, Sparkles, Trash2, MoreVertical, Info, Eye, X, AlertCircle } from 'lucide-react';
import Logo from './components/Logo';
import Uploader from './components/Uploader';
import DataTable from './components/DataTable';
import RetroWindow, { Sparkle } from './components/RetroWindow';
import { API_BASE_URL, getAuthHeaders } from './config/api';

interface QueryResult {
  columns: string[];
  results: Record<string, unknown>[];
  table_used: string;
  tables_used?: string[];
  cache_hit?: boolean;
  partitions_scanned?: number;
  partitions_total?: number;
}

interface TableMeta {
  name: string;
  columns: string[];
  record_count: number;
  source_format?: string | null;
}

interface QueryHistoryEntry {
  query: string;
  at: string;
  row_count?: number;
  cache_hit?: boolean;
}

interface TableDetails {
  name: string;
  columns: string[];
  column_count: number;
  record_count: number;
  schema?: Record<string, string>;
  source_format?: string | null;
  uploaded_at?: string | null;
  last_queries: QueryHistoryEntry[];
}

interface FileViewData {
  columns: string[];
  results: Record<string, unknown>[];
  source_format?: string | null;
}

interface CompletionPosition {
  lineNumber: number;
  column: number;
}

interface CompletionModel {
  getWordUntilPosition: (position: CompletionPosition) => {
    startColumn: number;
    endColumn: number;
  };
}

interface CompletionRange {
  startLineNumber: number;
  endLineNumber: number;
  startColumn: number;
  endColumn: number;
}

interface CompletionItem {
  label: string;
  kind: number;
  insertText: string;
  detail?: string;
  range: CompletionRange;
}

type DownloadFormat = 'csv' | 'json' | 'xlsx';
type AIAssistMode = 'optimize_query' | 'generate_from_english' | 'fix_sql_error';

interface AIAssistResult {
  suggested_query: string;
  explanation: string;
}

const AI_MODES: Array<{ id: AIAssistMode; label: string; placeholder: string }> = [
  {
    id: 'optimize_query',
    label: 'Optimize query',
    placeholder: 'Paste a SQL query to optimize, or leave this empty to optimize the current editor query.',
  },
  {
    id: 'generate_from_english',
    label: 'Generate from plain English',
    placeholder: 'Describe what data you want. Example: Show the top 10 customers by revenue.',
  },
  {
    id: 'fix_sql_error',
    label: 'Fix the last error',
    placeholder: 'Optionally add more context. The assistant will use the last failed query and error.',
  },
];

const getCellValue = (value: unknown): string => {
  if (value === null || value === undefined) {
    return '';
  }

  if (value instanceof Date) {
    return value.toISOString();
  }

  return String(value);
};

const escapeCsvValue = (value: unknown): string => {
  const text = getCellValue(value);

  if (!/[",\n\r]/.test(text)) {
    return text;
  }

  return `"${text.replaceAll('"', '""')}"`;
};

const escapeXmlValue = (value: unknown): string => getCellValue(value)
  .replaceAll('&', '&amp;')
  .replaceAll('<', '&lt;')
  .replaceAll('>', '&gt;')
  .replaceAll('"', '&quot;')
  .replaceAll("'", '&apos;');

const getColumnName = (columnIndex: number): string => {
  let index = columnIndex + 1;
  let columnName = '';

  while (index > 0) {
    const remainder = (index - 1) % 26;
    columnName = String.fromCharCode(65 + remainder) + columnName;
    index = Math.floor((index - 1) / 26);
  }

  return columnName;
};

const downloadBlob = (blob: Blob, filename: string): void => {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
};

const buildCsv = (columns: string[], rows: Record<string, unknown>[]): string => {
  const header = columns.map(escapeCsvValue).join(',');
  const body = rows.map(row => columns.map(column => escapeCsvValue(row[column])).join(','));

  return [header, ...body].join('\n');
};

const buildXlsx = (columns: string[], rows: Record<string, unknown>[]): Uint8Array => {
  const allRows = [
    columns,
    ...rows.map(row => columns.map(column => getCellValue(row[column]))),
  ];
  const sheetRows = allRows.map((row, rowIndex) => {
    const cells = row.map((cell, columnIndex) => {
      const cellRef = `${getColumnName(columnIndex)}${rowIndex + 1}`;

      return `<c r="${cellRef}" t="inlineStr"><is><t>${escapeXmlValue(cell)}</t></is></c>`;
    }).join('');

    return `<row r="${rowIndex + 1}">${cells}</row>`;
  }).join('');
  const worksheet = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>${sheetRows}</sheetData></worksheet>`;
  const workbook = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Query Results" sheetId="1" r:id="rId1"/></sheets></workbook>`;
  const workbookRels = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>`;
  const rootRels = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>`;
  const contentTypes = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>`;

  return zipSync({
    '[Content_Types].xml': strToU8(contentTypes),
    '_rels/.rels': strToU8(rootRels),
    'xl/workbook.xml': strToU8(workbook),
    'xl/_rels/workbook.xml.rels': strToU8(workbookRels),
    'xl/worksheets/sheet1.xml': strToU8(worksheet),
  });
};

// Monaco theme for the retro desktop: ink on the cream window, brick
// keywords. Every token colour clears 4.5:1 on #FFFAF3.
const defineEditorTheme: BeforeMount = (monaco) => {
  monaco.editor.defineTheme('spymonkRetro', {
    base: 'vs',
    inherit: true,
    rules: [
      { token: '', foreground: '201A3A' },
      { token: 'keyword', foreground: 'A63426', fontStyle: 'bold' },
      { token: 'operator', foreground: '7E4596' },
      { token: 'predefined', foreground: '7E4596' },
      { token: 'string.sql', foreground: '1F6B74' },
      { token: 'string', foreground: '1F6B74' },
      { token: 'number', foreground: '8F4A00' },
      { token: 'comment', foreground: '6E6475', fontStyle: 'italic' },
      { token: 'string.key.json', foreground: '7E4596' },
      { token: 'string.value.json', foreground: '1F6B74' },
      { token: 'keyword.json', foreground: 'A63426' },
    ],
    colors: {
      'editor.background': '#FFFAF3',
      'editor.foreground': '#201A3A',
      'editorLineNumber.foreground': '#6E6475',
      'editorLineNumber.activeForeground': '#201A3A',
      'editor.selectionBackground': '#F2B75580',
      'editor.inactiveSelectionBackground': '#F2B75540',
      'editor.lineHighlightBackground': '#FBEEDD',
      'editor.lineHighlightBorder': '#00000000',
      'editorCursor.foreground': '#A63426',
      'editorIndentGuide.background': '#EAD6C6',
      'editorIndentGuide.activeBackground': '#C9B1A0',
      'editorGutter.background': '#FFF4E7',
      'editorWidget.background': '#FFFAF3',
      'editorWidget.border': '#201A3A',
      'editorSuggestWidget.background': '#FFFAF3',
      'editorSuggestWidget.border': '#201A3A',
      'editorSuggestWidget.selectedBackground': '#FDE7C2',
      'editorSuggestWidget.selectedForeground': '#201A3A',
      'editorSuggestWidget.highlightForeground': '#A63426',
      'scrollbarSlider.background': '#F79D4799',
      'scrollbarSlider.hoverBackground': '#F79D47CC',
      'scrollbarSlider.activeBackground': '#F05B43CC',
    },
  });
};

function App() {
  const [query, setQuery] = useState('SELECT * FROM \nWHERE \nLIMIT 50;');
  const [isExecuting, setIsExecuting] = useState(false);
  const [queryResult, setQueryResult] = useState<QueryResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tables, setTables] = useState<TableMeta[]>([]);
  const tablesRef = useRef<TableMeta[]>([]);
  const abortControllerRef = useRef<AbortController | null>(null);
  const activeQueryIdRef = useRef<string | null>(null);
  const [isAiPanelOpen, setIsAiPanelOpen] = useState(false);
  const [aiMode, setAiMode] = useState<AIAssistMode>('optimize_query');
  const [aiInput, setAiInput] = useState('');
  const [isAiLoading, setIsAiLoading] = useState(false);
  const [aiResult, setAiResult] = useState<AIAssistResult | null>(null);
  const [aiError, setAiError] = useState<string | null>(null);
  const [lastExecutedQuery, setLastExecutedQuery] = useState('');
  const [lastQueryError, setLastQueryError] = useState<string | null>(null);
  const [expandedColumns, setExpandedColumns] = useState<Set<number>>(new Set());
  const [confirmDeleteTable, setConfirmDeleteTable] = useState<string | null>(null);
  const [deletingTable, setDeletingTable] = useState<string | null>(null);
  const [openMenuTable, setOpenMenuTable] = useState<string | null>(null);
  const [detailsFor, setDetailsFor] = useState<string | null>(null);
  const [details, setDetails] = useState<TableDetails | null>(null);
  const [detailsLoading, setDetailsLoading] = useState(false);
  const [detailsError, setDetailsError] = useState<string | null>(null);
  const [viewingFile, setViewingFile] = useState<string | null>(null);
  const [viewData, setViewData] = useState<FileViewData | null>(null);
  const [viewLoading, setViewLoading] = useState(false);
  const [viewError, setViewError] = useState<string | null>(null);

  const COLUMNS_PREVIEW_LIMIT = 5;

  const toggleColumnExpansion = (tableIdx: number) => {
    setExpandedColumns(prev => {
      const next = new Set(prev);
      if (next.has(tableIdx)) {
        next.delete(tableIdx);
      } else {
        next.add(tableIdx);
      }
      return next;
    });
  };

  // Update ref whenever tables state changes
  useEffect(() => {
    tablesRef.current = tables;
  }, [tables]);

  const fetchTables = async () => {
    try {
      const res = await fetch(`${API_BASE_URL}/tables`, {
        headers: getAuthHeaders(),
      });
      if (res.ok) {
        const data = await res.json();
        setTables(data.tables || []);
      }
    } catch (e) {
      console.error("Failed to fetch tables", e);
    }
  };

  useEffect(() => {
    fetchTables();
  }, []);

  const handleUploadSuccess = (data: { table_name: string }) => {
    setQuery(`SELECT * FROM ${data.table_name} LIMIT 50;`);
    setError(null);
    setQueryResult(null);
    fetchTables();
  };

  // Close the table-actions menu on any click outside it.
  useEffect(() => {
    if (!openMenuTable) return;
    const onDown = (e: MouseEvent) => {
      if (!(e.target as Element).closest('[data-menu-root]')) {
        setOpenMenuTable(null);
      }
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [openMenuTable]);

  // Escape closes (in priority order) the menu, the details modal, the file view.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      if (openMenuTable) setOpenMenuTable(null);
      else if (detailsFor) { setDetailsFor(null); setDetails(null); setDetailsError(null); }
      else if (viewingFile) { setViewingFile(null); setViewData(null); setViewError(null); }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [openMenuTable, detailsFor, viewingFile]);

  const closeDetails = () => {
    setDetailsFor(null);
    setDetails(null);
    setDetailsError(null);
  };

  const openDetails = async (tableName: string) => {
    setOpenMenuTable(null);
    setDetailsFor(tableName);
    setDetails(null);
    setDetailsError(null);
    setDetailsLoading(true);

    try {
      const response = await fetch(`${API_BASE_URL}/tables/${tableName}`, {
        headers: getAuthHeaders(),
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || 'Failed to load table details');
      }
      setDetails(data);
    } catch (err: unknown) {
      setDetailsError(err instanceof Error ? err.message : 'Failed to load table details');
    } finally {
      setDetailsLoading(false);
    }
  };

  const closeFileView = () => {
    setViewingFile(null);
    setViewData(null);
    setViewError(null);
  };

  const openFileView = async (tableName: string) => {
    setOpenMenuTable(null);
    setViewingFile(tableName);
    setViewData(null);
    setViewError(null);
    setViewLoading(true);

    try {
      const response = await fetch(`${API_BASE_URL}/tables/${tableName}/data`, {
        headers: getAuthHeaders(),
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || 'Failed to load file contents');
      }
      setViewData({ columns: data.columns, results: data.results, source_format: data.source_format });
    } catch (err: unknown) {
      setViewError(err instanceof Error ? err.message : 'Failed to load file contents');
    } finally {
      setViewLoading(false);
    }
  };

  const handleDeleteTable = async (tableName: string) => {
    setDeletingTable(tableName);
    setError(null);

    try {
      const response = await fetch(`${API_BASE_URL}/tables/${tableName}`, {
        method: 'DELETE',
        headers: getAuthHeaders(),
      });
      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail || `Failed to delete table '${tableName}'`);
      }

      // Server-side, deletion bumps the table version so cached results for it
      // can never be served again. Here we just drop any on-screen result that
      // came from the deleted table (JOINs included).
      setQueryResult(prev => {
        if (!prev) return prev;
        const usedTables = prev.tables_used ?? [prev.table_used];
        return usedTables.includes(tableName) ? null : prev;
      });
      if (viewingFile === tableName) closeFileView();
      if (detailsFor === tableName) closeDetails();
      await fetchTables();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : `Failed to delete table '${tableName}'`);
    } finally {
      setDeletingTable(null);
      setConfirmDeleteTable(null);
    }
  };

  const handleRunQuery = async () => {
    if (isExecuting || !query.trim()) {
      return;
    }

    const queryId = crypto.randomUUID();
    const abortController = new AbortController();

    activeQueryIdRef.current = queryId;
    abortControllerRef.current = abortController;
    setLastExecutedQuery(query);
    setIsExecuting(true);
    setError(null);
    setQueryResult(null);

    try {
      const response = await fetch(`${API_BASE_URL}/query`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...getAuthHeaders(),
        },
        body: JSON.stringify({ query, query_id: queryId }),
        signal: abortController.signal,
      });

      const data = await response.json();

      if (!response.ok || !data.success) {
        throw new Error(data.error || data.detail || 'Query execution failed');
      }

      setQueryResult({
        columns: data.columns,
        results: data.results,
        table_used: data.table_used,
        tables_used: data.tables_used,
        cache_hit: data.cache_hit,
        partitions_scanned: data.partitions_scanned,
        partitions_total: data.partitions_total,
      });
      setLastQueryError(null);

    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === 'AbortError') {
        setError('Query cancelled.');
        return;
      }

      const errorMessage = err instanceof Error ? err.message : 'Query execution failed';
      setLastQueryError(errorMessage);
      setError(errorMessage);
    } finally {
      activeQueryIdRef.current = null;
      abortControllerRef.current = null;
      setIsExecuting(false);
    }
  };

  // Keep a live reference so the editor's Cmd/Ctrl+Enter shortcut always runs
  // the current query, not the one captured when the editor mounted.
  const runQueryRef = useRef(handleRunQuery);
  useEffect(() => {
    runQueryRef.current = handleRunQuery;
  });

  const handleCancelQuery = async () => {
    const queryId = activeQueryIdRef.current;

    if (!queryId) {
      return;
    }

    try {
      await fetch(`${API_BASE_URL}/query/cancel/${queryId}`, {
        method: 'POST',
        headers: getAuthHeaders(),
      });
    } catch (err) {
      console.error('Failed to cancel query', err);
    } finally {
      abortControllerRef.current?.abort();
      setError('Query cancelled.');
    }
  };

  const handleDownloadResults = (format: DownloadFormat) => {
    if (!queryResult) {
      return;
    }

    const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
    const filename = `${queryResult.table_used}_query_results_${timestamp}.${format}`;

    if (format === 'csv') {
      downloadBlob(
        new Blob([buildCsv(queryResult.columns, queryResult.results)], { type: 'text/csv;charset=utf-8' }),
        filename,
      );
      return;
    }

    if (format === 'json') {
      downloadBlob(
        new Blob([JSON.stringify(queryResult.results, null, 2)], { type: 'application/json;charset=utf-8' }),
        filename,
      );
      return;
    }

    const workbook = buildXlsx(queryResult.columns, queryResult.results);
    const workbookBytes = new Uint8Array(workbook);

    downloadBlob(
      new Blob([workbookBytes.buffer], {
        type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      }),
      filename,
    );
  };

  const handleSubmitAiRequest = async () => {
    const trimmedInput = aiInput.trim();
    const currentQuery = aiMode === 'fix_sql_error' ? lastExecutedQuery || query : query;

    if (aiMode === 'generate_from_english' && !trimmedInput) {
      setAiError('Describe the query you want to generate.');
      return;
    }

    if (aiMode === 'fix_sql_error' && !lastQueryError && !trimmedInput) {
      setAiError('Run a failing query first, or describe the SQL error.');
      return;
    }

    setIsAiLoading(true);
    setAiError(null);
    setAiResult(null);

    try {
      const response = await fetch(`${API_BASE_URL}/ai/assist`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...getAuthHeaders(),
        },
        body: JSON.stringify({
          mode: aiMode,
          user_input: trimmedInput,
          current_query: currentQuery,
          last_error: aiMode === 'fix_sql_error' ? lastQueryError : null,
          table_context: tables.map(table => ({
            name: table.name,
            columns: table.columns,
            record_count: table.record_count,
          })),
        }),
      });
      const data = await response.json();

      if (!response.ok || !data.success) {
        throw new Error(data.error || data.detail || 'AI assistant failed');
      }

      setAiResult({
        suggested_query: data.suggested_query,
        explanation: data.explanation || '',
      });
    } catch (err: unknown) {
      setAiError(err instanceof Error ? err.message : 'AI assistant failed');
    } finally {
      setIsAiLoading(false);
    }
  };

  const handleApplyAiQuery = () => {
    if (!aiResult?.suggested_query) {
      return;
    }

    setQuery(aiResult.suggested_query);
    setError(null);
    setQueryResult(null);
  };

  const handleEditorDidMount: OnMount = (editor, monaco) => {
    // Run the current query with Cmd/Ctrl + Enter.
    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter, () => {
      runQueryRef.current();
    });

    // Register SQL auto-completion
    monaco.languages.registerCompletionItemProvider('sql', {
      provideCompletionItems: (model: CompletionModel, position: CompletionPosition) => {
        const word = model.getWordUntilPosition(position);
        const range = {
          startLineNumber: position.lineNumber,
          endLineNumber: position.lineNumber,
          startColumn: word.startColumn,
          endColumn: word.endColumn,
        };

        const currentTables = tablesRef.current;
        const items: CompletionItem[] = [];

        // Add table names to suggestions
        currentTables.forEach(table => {
          items.push({
            label: table.name,
            kind: monaco.languages.CompletionItemKind.Struct,
            insertText: table.name,
            detail: `Table (${table.record_count} records)`,
            range: range,
          });

          // Add column names for each table
          table.columns.forEach(col => {
            items.push({
              label: col,
              kind: monaco.languages.CompletionItemKind.Field,
              insertText: col,
              detail: `Column in ${table.name}`,
              range: range,
            });
          });
        });

        // Basic SQL Keywords
        const keywords = ['SELECT', 'FROM', 'WHERE', 'LIMIT', 'ORDER BY', 'GROUP BY', 'JOIN', 'LEFT JOIN', 'INNER JOIN', 'ON', 'AS', 'COUNT', 'SUM', 'AVG', 'MAX', 'MIN', 'DESC', 'ASC'];
        keywords.forEach(kw => {
          items.push({
            label: kw,
            kind: monaco.languages.CompletionItemKind.Keyword,
            insertText: kw,
            range: range,
          });
        });

        return { suggestions: items };
      },
    });
  };

  const canRun = !isExecuting && !!query.trim();
  const totalRows = tables.reduce((sum, table) => sum + table.record_count, 0);
  // Cancelling is the user's own action, so it gets the quiet tan notice
  // rather than the orange "Error" window.
  const queryCancelled = error === 'Query cancelled.';

  return (
    <div className="app">
      {/* Taskbar */}
      <header className="taskbar">
        <Logo size={34} style={{ color: 'var(--logo)', display: 'block' }} />
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.15rem' }}>
          <h1 className="taskbar-title">
            spyMonk <span>warehouse</span>
          </h1>
          <span className="eyebrow">upload · query · explore your data</span>
        </div>
        <div className="taskbar-status">
          <span className="chip chip--cream chip--raised">
            {tables.length} {tables.length === 1 ? 'table' : 'tables'} · {totalRows.toLocaleString()} rows
          </span>
        </div>
      </header>

      {/* The desk */}
      <div className="desk">

        {/* Left column: ingest + the table "hard disk" */}
        <aside className="desk-col desk-col--side" aria-label="Data sources">
          <RetroWindow title="Ingest data" tone="orange" className="win--fixed" bodyClassName="win-body--pad">
            <Uploader onUploadSuccess={handleUploadSuccess} />
          </RetroWindow>

          <RetroWindow title="Available tables" tone="lavender" className="tables-window" bodyClassName="win-body--scroll">
            {tables.length === 0 ? (
              <div className="state" style={{ padding: '1.75rem 1rem' }}>
                <span className="folder folder--lg" aria-hidden="true" />
                <p className="state-text">No tables yet. Upload a file above to get started.</p>
              </div>
            ) : (
              <div className="table-list">
                {tables.map((t, idx) => {
                  const confirming = confirmDeleteTable === t.name;
                  const deleting = deletingTable === t.name;
                  return (
                  <div key={idx} className={confirming ? 'tcard tcard--confirm' : 'tcard'}>
                    {confirming ? (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.65rem' }}>
                        <div style={{ fontSize: '0.86rem', lineHeight: 1.45 }}>
                          Delete <code style={{ wordBreak: 'break-all' }}>{t.name}</code>?
                        </div>
                        <p style={{ fontSize: '0.76rem', color: 'var(--ink-1)', lineHeight: 1.5 }}>
                          Removes the table and all {t.record_count.toLocaleString()} rows. This can’t be undone.
                        </p>
                        <div style={{ display: 'flex', gap: '0.5rem' }}>
                          <button
                            type="button"
                            className="btn btn-sm btn-block"
                            disabled={deleting}
                            onClick={() => setConfirmDeleteTable(null)}
                            aria-label={`Keep table ${t.name}`}
                          >
                            Cancel
                          </button>
                          <button
                            type="button"
                            className="btn btn-sm btn-block btn-danger"
                            disabled={deleting}
                            aria-busy={deleting}
                            onClick={() => handleDeleteTable(t.name)}
                            aria-label={`Confirm delete table ${t.name}`}
                          >
                            {deleting ? 'Deleting…' : 'Delete'}
                          </button>
                        </div>
                      </div>
                    ) : (
                      <>
                        <div className="tcard-head">
                          <span className="folder" aria-hidden="true" />
                          <span className="mono-name" title={t.name} style={{ flex: 1 }}>
                            {t.name}
                          </span>
                          <span className="meta-mono">{t.record_count.toLocaleString()} rows</span>
                          <div style={{ position: 'relative' }} data-menu-root>
                            <button
                              type="button"
                              className="btn-ghost-icon"
                              onClick={() => setOpenMenuTable(open => (open === t.name ? null : t.name))}
                              aria-label={`Actions for table ${t.name}`}
                              aria-haspopup="menu"
                              aria-expanded={openMenuTable === t.name}
                              title="Table actions"
                            >
                              <MoreVertical aria-hidden="true" />
                            </button>
                            {openMenuTable === t.name && (
                              <div role="menu" aria-label={`Actions for table ${t.name}`} className="menu">
                                {([
                                  { label: 'Details', icon: Info, danger: false, onPick: () => openDetails(t.name) },
                                  { label: 'View file', icon: Eye, danger: false, onPick: () => openFileView(t.name) },
                                  { label: 'Delete file', icon: Trash2, danger: true, onPick: () => { setOpenMenuTable(null); setConfirmDeleteTable(t.name); } },
                                ]).map(item => (
                                  <button
                                    key={item.label}
                                    type="button"
                                    role="menuitem"
                                    onClick={item.onPick}
                                    className={item.danger ? 'menu-item menu-item--danger' : 'menu-item'}
                                  >
                                    <item.icon aria-hidden="true" />
                                    {item.label}
                                  </button>
                                ))}
                              </div>
                            )}
                          </div>
                        </div>
                        <div className="eyebrow" style={{ marginBottom: '0.4rem' }}>
                          Columns
                          <span style={{ marginLeft: '0.35rem', fontWeight: 400, textTransform: 'none', letterSpacing: 0 }}>
                            ({t.columns.length})
                          </span>
                        </div>
                        {(() => {
                          const isExpanded = expandedColumns.has(idx);
                          const visibleCols = isExpanded ? t.columns : t.columns.slice(0, COLUMNS_PREVIEW_LIMIT);
                          const hasMore = t.columns.length > COLUMNS_PREVIEW_LIMIT;
                          return (
                            <>
                              <div className={isExpanded ? 'tcard-tags tcard-tags--expanded' : 'tcard-tags'}>
                                {visibleCols.map((col, cIdx) => (
                                  <span key={cIdx} className="tag">{col}</span>
                                ))}
                              </div>
                              {hasMore && (
                                <button
                                  type="button"
                                  className="btn-link"
                                  style={{ marginTop: '0.45rem' }}
                                  onClick={() => toggleColumnExpansion(idx)}
                                  aria-expanded={isExpanded}
                                >
                                  {isExpanded
                                    ? '▲ Show less'
                                    : `▼ Show ${t.columns.length - COLUMNS_PREVIEW_LIMIT} more`}
                                </button>
                              )}
                            </>
                          );
                        })()}
                      </>
                    )}
                  </div>
                  );
                })}
              </div>
            )}
          </RetroWindow>
        </aside>

        {/* Right column: editor + results, or the file viewer */}
        <main className="desk-col desk-col--main">
          {viewingFile ? (
            <RetroWindow title="File contents" tone="tan" className="results-window" bodyClassName="viewer-body">
              {/* File viewer — replaces the editor and results windows */}
              <div className="win-strip">
                <div className="win-strip-group">
                  <span className="mono-name">{viewingFile}</span>
                  {viewData?.source_format && (
                    <span className="chip chip--lavender">{viewData.source_format}</span>
                  )}
                  {viewData && (
                    <span className="meta-mono">{viewData.results.length.toLocaleString()} rows</span>
                  )}
                </div>
                <button
                  type="button"
                  className="btn btn-sm"
                  onClick={closeFileView}
                  aria-label="Close file view and return to the SQL editor"
                >
                  <X aria-hidden="true" />
                  Back to editor
                </button>
              </div>

              {viewLoading && (
                <div className="state" role="status">
                  <div className="loader" aria-hidden="true"><span /></div>
                  <span className="state-text">Loading file contents…</span>
                </div>
              )}

              {!viewLoading && viewError && (
                <div style={{ padding: '1.1rem' }}>
                  <div className="message message-error" role="alert">
                    <AlertCircle aria-hidden="true" />
                    {viewError}
                  </div>
                </div>
              )}

              {!viewLoading && !viewError && viewData && (
                viewData.source_format === 'json' ? (
                  <div style={{ flex: 1, minHeight: 0 }}>
                    <Editor
                      height="100%"
                      defaultLanguage="json"
                      theme="spymonkRetro"
                      beforeMount={defineEditorTheme}
                      value={JSON.stringify(viewData.results, null, 2)}
                      options={{
                        readOnly: true,
                        domReadOnly: true,
                        minimap: { enabled: false },
                        scrollBeyondLastLine: false,
                        fontSize: 13,
                        padding: { top: 14, bottom: 14 },
                        fontFamily: "'JetBrains Mono', ui-monospace, monospace",
                        lineNumbers: 'on',
                        folding: true,
                        wordWrap: 'off',
                        scrollbar: { verticalScrollbarSize: 10, horizontalScrollbarSize: 10 },
                        automaticLayout: true,
                      }}
                    />
                  </div>
                ) : (
                  <DataTable
                    key={`view_${viewingFile}_${viewData.results.length}`}
                    columns={viewData.columns}
                    data={viewData.results}
                  />
                )
              )}
            </RetroWindow>
          ) : (
            <>
              <RetroWindow title="SQL editor" tone="coral" className="win--fixed">
                <div className="win-strip">
                  <span className="eyebrow">query.sql · ⌘/Ctrl + Enter runs it</span>
                  <div className="win-strip-group">
                    <button
                      type="button"
                      className="btn btn-sm"
                      onClick={() => {
                        setIsAiPanelOpen(value => !value);
                        setAiError(null);
                      }}
                      aria-label="AI assistant"
                      aria-pressed={isAiPanelOpen}
                      title="AI assistant"
                    >
                      <Sparkles aria-hidden="true" />
                      Assistant
                    </button>

                    {isExecuting && (
                      <button
                        type="button"
                        className="btn btn-sm btn-danger-soft"
                        onClick={handleCancelQuery}
                        aria-label="Cancel running query"
                      >
                        <Square aria-hidden="true" />
                        Cancel
                      </button>
                    )}

                    <button
                      type="button"
                      className="btn btn-sm btn-primary"
                      onClick={handleRunQuery}
                      disabled={!canRun}
                      aria-busy={isExecuting}
                      aria-label="Run SQL query"
                      title="Run query  (⌘/Ctrl + Enter)"
                    >
                      {isExecuting ? (
                        <span className="loading-spinner" aria-hidden="true" />
                      ) : (
                        <Play aria-hidden="true" />
                      )}
                      {isExecuting ? 'Running…' : 'Run query'}
                    </button>
                  </div>
                </div>

                {isAiPanelOpen && (
                  <section className="assistant" aria-labelledby="assistant-title">
                    <div className="assistant-head">
                      <h3 id="assistant-title" className="assistant-title">
                        <Sparkles aria-hidden="true" />
                        AI SQL assistant
                      </h3>
                      <span className="muted" style={{ fontSize: '0.76rem' }}>
                        Optimize, generate, and fix — SELECT queries only.
                      </span>
                    </div>

                    <div className="assistant-modes">
                      {AI_MODES.map(mode => (
                        <button
                          key={mode.id}
                          type="button"
                          className="mode"
                          aria-pressed={aiMode === mode.id}
                          onClick={() => {
                            setAiMode(mode.id);
                            setAiResult(null);
                            setAiError(null);
                          }}
                        >
                          <span className="mode-dot" aria-hidden="true" />
                          {mode.label}
                        </button>
                      ))}
                    </div>

                    <textarea
                      className="field"
                      value={aiInput}
                      onChange={(event) => setAiInput(event.target.value)}
                      placeholder={AI_MODES.find(mode => mode.id === aiMode)?.placeholder}
                      rows={3}
                      aria-label="AI assistant prompt"
                    />

                    {aiMode === 'fix_sql_error' && lastQueryError && (
                      <p className="assistant-last-error">Last error: {lastQueryError}</p>
                    )}

                    <div className="assistant-actions">
                      <button
                        type="button"
                        className="btn btn-primary"
                        onClick={handleSubmitAiRequest}
                        disabled={isAiLoading}
                        aria-busy={isAiLoading}
                      >
                        <Sparkles aria-hidden="true" />
                        {isAiLoading ? 'Resolving…' : 'Resolve'}
                      </button>

                      {aiResult && (
                        <button type="button" className="btn" onClick={handleApplyAiQuery}>
                          Apply to editor
                        </button>
                      )}
                    </div>

                    {aiError && (
                      <div className="message message-error" role="alert" style={{ marginTop: '0.8rem' }}>
                        <AlertCircle aria-hidden="true" />
                        {aiError}
                      </div>
                    )}

                    {aiResult && (
                      <div className="suggestion">
                        <div className="eyebrow">Suggested query</div>
                        <pre>{aiResult.suggested_query}</pre>
                        {aiResult.explanation && <p>{aiResult.explanation}</p>}
                      </div>
                    )}
                  </section>
                )}

                {/* Monaco Editor */}
                <div className="editor-pane">
                  <Editor
                    height="100%"
                    defaultLanguage="sql"
                    theme="spymonkRetro"
                    beforeMount={defineEditorTheme}
                    value={query}
                    onMount={handleEditorDidMount}
                    onChange={(value) => setQuery(value || '')}
                    options={{
                      minimap: { enabled: false },
                      scrollBeyondLastLine: false,
                      fontSize: 14,
                      padding: { top: 16, bottom: 16 },
                      fontFamily: "'JetBrains Mono', ui-monospace, monospace",
                      fontLigatures: true,
                      renderLineHighlight: 'all',
                      scrollbar: { verticalScrollbarSize: 10, horizontalScrollbarSize: 10 },
                      automaticLayout: true,
                      suggestOnTriggerCharacters: true,
                      quickSuggestions: { other: true, comments: false, strings: true },
                      wordBasedSuggestions: "off",
                      lineNumbers: "on",
                    }}
                  />
                </div>
              </RetroWindow>

              {/* Results */}
              <RetroWindow title="Results" tone="tan" className="results-window" bodyClassName="results-body">
                {error && (
                  <div className={queryCancelled ? 'alert-win alert-win--quiet' : 'alert-win'} role="alert">
                    <div className="alert-win-bar">{queryCancelled ? 'Cancelled' : 'Error'}</div>
                    <div className="alert-win-body">
                      <span className="alert-icon" aria-hidden="true">!</span>
                      <p>{error}</p>
                    </div>
                  </div>
                )}

                {!error && queryResult && (
                  <div className="rise-in">
                    <div className="win-strip">
                      <div className="win-strip-group">
                        <span className="chip chip--cream">
                          {queryResult.results.length.toLocaleString()} rows
                        </span>
                        {queryResult.cache_hit && <span className="chip chip--ok">cached</span>}
                        {typeof queryResult.partitions_total === 'number' && queryResult.partitions_total > 0 && (
                          <span className="meta-mono">
                            scanned {queryResult.partitions_scanned}/{queryResult.partitions_total} partitions
                          </span>
                        )}
                      </div>
                      <div className="win-strip-group">
                        {(['csv', 'json', 'xlsx'] as DownloadFormat[]).map(format => (
                          <button
                            key={format}
                            type="button"
                            className="btn btn-sm"
                            onClick={() => handleDownloadResults(format)}
                            aria-label={`Download query results as ${format.toUpperCase()}`}
                            title={`Download ${format.toUpperCase()}`}
                          >
                            <Download aria-hidden="true" />
                            {format.toUpperCase()}
                          </button>
                        ))}
                      </div>
                    </div>

                    <DataTable
                      key={`${queryResult.table_used}_${queryResult.results.length}`}
                      columns={queryResult.columns}
                      data={queryResult.results}
                    />
                  </div>
                )}

                {!error && !queryResult && isExecuting && (
                  <div className="state" role="status">
                    <div className="loader" aria-hidden="true"><span /></div>
                    <p className="state-text">Running query…</p>
                  </div>
                )}

                {!error && !queryResult && !isExecuting && (
                  <div className="state">
                    <div className="state-art" aria-hidden="true">
                      <div className="mini-win">
                        <div className="mini-win-bar" />
                        <div className="mini-win-body"><Database /></div>
                      </div>
                      <Sparkle size={22} style={{ top: -16, right: -22 }} />
                      <Sparkle size={14} style={{ bottom: 4, left: -22 }} />
                    </div>
                    <p className="state-title">Ready when you are</p>
                    <p className="state-text">
                      Write a query and press <strong>Run query</strong> — or <kbd>⌘/Ctrl</kbd> + <kbd>Enter</kbd>.
                    </p>
                  </div>
                )}
              </RetroWindow>
            </>
          )}
        </main>
      </div>

      {/* Table details dialog */}
      {detailsFor && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label={`Details for table ${detailsFor}`}
          className="overlay"
          onMouseDown={e => { if (e.target === e.currentTarget) closeDetails(); }}
        >
          <RetroWindow
            title={
              <>
                Table details
                <span className="mono-name" style={{ marginLeft: '0.5rem', fontSize: '0.82rem' }}>{detailsFor}</span>
              </>
            }
            tone="orange"
            className="dialog dialog--stacked"
            bodyClassName="dialog-body"
            actions={
              <button type="button" onClick={closeDetails} aria-label="Close details" className="btn-icon btn-icon--bar">
                <X aria-hidden="true" />
              </button>
            }
          >
            {detailsLoading && (
              <div className="state" role="status" style={{ padding: '2rem 0' }}>
                <div className="loader" aria-hidden="true"><span /></div>
                <span className="state-text">Loading details…</span>
              </div>
            )}

            {detailsError && !detailsLoading && (
              <div className="message message-error" role="alert">
                <AlertCircle aria-hidden="true" />
                {detailsError}
              </div>
            )}

            {details && !detailsLoading && (
              <>
                <div className="stats">
                  {[
                    { label: 'Total rows', value: details.record_count.toLocaleString() },
                    { label: 'Columns', value: String(details.column_count) },
                    { label: 'Format', value: details.source_format ? details.source_format.toUpperCase() : '—' },
                    { label: 'Uploaded', value: details.uploaded_at ? new Date(details.uploaded_at).toLocaleString() : '—' },
                  ].map(stat => (
                    <div key={stat.label} className="stat">
                      <div className="eyebrow">{stat.label}</div>
                      <div className="stat-value">{stat.value}</div>
                    </div>
                  ))}
                </div>

                <section>
                  <h3 className="eyebrow section-label">Table definitions</h3>
                  <div className="schema">
                    {details.columns.map(col => (
                      <div key={col} className="schema-row">
                        <span className="schema-name">{col}</span>
                        <span className="schema-type">{details.schema?.[col] ?? '—'}</span>
                      </div>
                    ))}
                  </div>
                </section>

                <section>
                  <h3 className="eyebrow section-label">Last 5 queries</h3>
                  {details.last_queries.length === 0 ? (
                    <p className="muted" style={{ fontSize: '0.82rem' }}>
                      No queries have been run on this table yet.
                    </p>
                  ) : (
                    <div className="history">
                      {details.last_queries.map((q, i) => (
                        <div key={i} className="history-item">
                          <pre>{q.query}</pre>
                          <div className="history-meta">
                            <span>{new Date(q.at).toLocaleString()}</span>
                            {typeof q.row_count === 'number' && <span>{q.row_count.toLocaleString()} rows</span>}
                            {q.cache_hit && <span>served from cache</span>}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </section>
              </>
            )}
          </RetroWindow>
        </div>
      )}
    </div>
  );
}

export default App;
