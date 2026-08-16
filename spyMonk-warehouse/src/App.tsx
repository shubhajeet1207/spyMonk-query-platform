import { useState, useEffect, useRef } from 'react';
import Editor, { type OnMount, type BeforeMount } from '@monaco-editor/react';
import { strToU8, zipSync } from 'fflate';
import { Play, Database, FileText, Table, Square, Download, Sparkles, Trash2, MoreVertical, Info, Eye, X } from 'lucide-react';
import Logo from './components/Logo';
import Uploader from './components/Uploader';
import DataTable from './components/DataTable';
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

// Cool, low-contrast Monaco theme matching the pastel palette.
const defineEditorTheme: BeforeMount = (monaco) => {
  monaco.editor.defineTheme('spymonkLight', {
    base: 'vs',
    inherit: true,
    rules: [
      { token: '', foreground: '2E3547' },
      { token: 'keyword', foreground: '345F82', fontStyle: 'bold' },
      { token: 'operator', foreground: '345F82' },
      { token: 'predefined', foreground: '345F82' },
      { token: 'string.sql', foreground: '2E7D74' },
      { token: 'string', foreground: '2E7D74' },
      { token: 'number', foreground: 'A83E30' },
      { token: 'comment', foreground: '7C8698', fontStyle: 'italic' },
    ],
    colors: {
      'editor.background': '#FFFFFF',
      'editor.foreground': '#2E3547',
      'editorLineNumber.foreground': '#9AA3B4',
      'editorLineNumber.activeForeground': '#566072',
      'editor.selectionBackground': '#DCEAF4',
      'editor.lineHighlightBackground': '#F4F7FC',
      'editor.lineHighlightBorder': '#00000000',
      'editorCursor.foreground': '#3F6D91',
      'editorIndentGuide.background': '#E7ECF4',
      'editorIndentGuide.activeBackground': '#D3DBE8',
      'editorGutter.background': '#FFFFFF',
      'editorWidget.background': '#FFFFFF',
      'editorWidget.border': '#D3DBE8',
      'editorSuggestWidget.background': '#FFFFFF',
      'editorSuggestWidget.border': '#D3DBE8',
      'editorSuggestWidget.selectedBackground': '#DCEAF4',
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

  const sectionLabelStyle: React.CSSProperties = {
    fontSize: '0.72rem', fontWeight: 600, textTransform: 'uppercase',
    letterSpacing: '0.07em', color: 'var(--ink-2)',
  };

  return (
    <div style={{ minHeight: '100vh', background: 'var(--paper-0)', display: 'flex', flexDirection: 'column', fontFamily: 'var(--font-sans)', color: 'var(--ink-0)' }}>
      {/* Header */}
      <header style={{
        display: 'flex', alignItems: 'center', gap: '0.85rem',
        padding: '0.7rem 1.5rem',
        background: 'var(--paper-1)',
        borderBottom: '1px solid var(--line)',
      }}>
        <Logo size={34} style={{ color: 'var(--logo)', display: 'block' }} />
        <div style={{ display: 'flex', flexDirection: 'column', lineHeight: 1.1 }}>
          <span style={{ fontFamily: 'var(--font-display)', fontSize: '1.2rem', fontWeight: 600, color: 'var(--ink-0)', letterSpacing: '-0.01em' }}>
            spyMonk <span style={{ fontWeight: 400, color: 'var(--ink-1)' }}>warehouse</span>
          </span>
          <span style={{ fontSize: '0.7rem', color: 'var(--ink-2)', letterSpacing: '0.02em' }}>
            upload · query · explore your data
          </span>
        </div>
      </header>

      {/* Main Content */}
      <div style={{ flex: 1, display: 'grid', gridTemplateColumns: '288px 1fr', overflow: 'hidden' }}>

        {/* Left Sidebar */}
        <aside style={{
          background: 'var(--paper-1)',
          borderRight: '1px solid var(--line)',
          display: 'flex', flexDirection: 'column',
          overflowY: 'auto', padding: '1.35rem',
          gap: '1.75rem',
        }}>

          {/* Ingest Data */}
          <section>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.8rem' }}>
              <FileText style={{ width: 15, height: 15, color: 'var(--ink-2)' }} />
              <span style={sectionLabelStyle}>Ingest data</span>
            </div>
            <Uploader onUploadSuccess={handleUploadSuccess} />
          </section>

          {/* Available Tables */}
          <section style={{ flex: 1 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.8rem' }}>
              <Table style={{ width: 15, height: 15, color: 'var(--ink-2)' }} />
              <span style={sectionLabelStyle}>Available tables</span>
            </div>

            {tables.length === 0 ? (
              <p style={{ color: 'var(--ink-2)', fontSize: '0.82rem', lineHeight: 1.5, paddingLeft: '0.1rem' }}>
                No tables yet. Upload a file above to get started.
              </p>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                {tables.map((t, idx) => {
                  const confirming = confirmDeleteTable === t.name;
                  const deleting = deletingTable === t.name;
                  return (
                  <div key={idx} style={{
                    background: confirming ? 'var(--danger-soft)' : 'var(--paper-2)',
                    border: `1px solid ${confirming ? 'var(--danger-line)' : 'var(--line)'}`,
                    borderRadius: 'var(--radius-sm)',
                    padding: '0.8rem 0.85rem',
                    transition: 'border-color 0.15s ease, background 0.15s ease',
                  }}
                    onMouseEnter={confirming ? undefined : e => { e.currentTarget.style.borderColor = 'var(--accent-line)'; }}
                    onMouseLeave={confirming ? undefined : e => { e.currentTarget.style.borderColor = 'var(--line)'; }}
                  >
                    {confirming ? (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.7rem' }}>
                        <div style={{ fontSize: '0.83rem', color: 'var(--ink-0)', lineHeight: 1.45 }}>
                          Delete <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 600, wordBreak: 'break-all' }}>{t.name}</span>?
                        </div>
                        <div style={{ fontSize: '0.74rem', color: 'var(--ink-1)', lineHeight: 1.5 }}>
                          Removes the table and all {t.record_count.toLocaleString()} rows. This can’t be undone.
                        </div>
                        <div style={{ display: 'flex', gap: '0.5rem' }}>
                          <button
                            type="button"
                            disabled={deleting}
                            onClick={() => setConfirmDeleteTable(null)}
                            aria-label={`Keep table ${t.name}`}
                            style={{
                              flex: 1, padding: '0.42rem 0', borderRadius: '6px',
                              background: 'var(--paper-1)', border: '1px solid var(--line)',
                              color: 'var(--ink-0)', cursor: deleting ? 'not-allowed' : 'pointer',
                              fontSize: '0.77rem', fontWeight: 600, fontFamily: 'var(--font-sans)',
                            }}
                          >
                            Cancel
                          </button>
                          <button
                            type="button"
                            disabled={deleting}
                            onClick={() => handleDeleteTable(t.name)}
                            aria-label={`Confirm delete table ${t.name}`}
                            style={{
                              flex: 1, padding: '0.42rem 0', borderRadius: '6px',
                              background: 'var(--danger)', border: '1px solid var(--danger)',
                              color: '#fff', cursor: deleting ? 'wait' : 'pointer',
                              fontSize: '0.77rem', fontWeight: 600, fontFamily: 'var(--font-sans)',
                            }}
                          >
                            {deleting ? 'Deleting…' : 'Delete'}
                          </button>
                        </div>
                      </div>
                    ) : (
                      <>
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '0.5rem', marginBottom: '0.55rem' }}>
                          <span
                            title={t.name}
                            style={{
                              color: 'var(--ink-0)', fontSize: '0.85rem', fontWeight: 600,
                              fontFamily: 'var(--font-mono)',
                              flex: 1, minWidth: 0,
                              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                            }}
                          >
                            {t.name}
                          </span>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexShrink: 0 }}>
                            <span style={{ color: 'var(--ink-2)', fontSize: '0.72rem', fontFamily: 'var(--font-mono)' }}>{t.record_count.toLocaleString()} rows</span>
                            <div style={{ position: 'relative' }} data-menu-root>
                              <button
                                type="button"
                                onClick={() => setOpenMenuTable(open => (open === t.name ? null : t.name))}
                                aria-label={`Actions for table ${t.name}`}
                                aria-haspopup="menu"
                                aria-expanded={openMenuTable === t.name}
                                title="Table actions"
                                style={{
                                  display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                                  width: 24, height: 24,
                                  background: openMenuTable === t.name ? 'var(--paper-hover)' : 'none',
                                  border: 'none', borderRadius: 'var(--radius-xs)',
                                  color: 'var(--ink-2)', cursor: 'pointer', padding: 0,
                                  transition: 'color 0.15s ease, background 0.15s ease',
                                }}
                                onMouseEnter={e => { e.currentTarget.style.color = 'var(--ink-0)'; e.currentTarget.style.background = 'var(--paper-hover)'; }}
                                onMouseLeave={e => { e.currentTarget.style.color = 'var(--ink-2)'; if (openMenuTable !== t.name) e.currentTarget.style.background = 'none'; }}
                              >
                                <MoreVertical style={{ width: 15, height: 15 }} />
                              </button>
                              {openMenuTable === t.name && (
                                <div
                                  role="menu"
                                  aria-label={`Actions for table ${t.name}`}
                                  style={{
                                    position: 'absolute', right: 0, top: 'calc(100% + 4px)', zIndex: 30,
                                    minWidth: 160,
                                    background: 'var(--paper-1)',
                                    border: '1px solid var(--line)',
                                    borderRadius: 'var(--radius-sm)',
                                    boxShadow: 'var(--shadow-md)',
                                    padding: '0.3rem',
                                    display: 'flex', flexDirection: 'column', gap: '0.1rem',
                                  }}
                                >
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
                                      style={{
                                        display: 'flex', alignItems: 'center', gap: '0.5rem',
                                        width: '100%', padding: '0.42rem 0.55rem',
                                        background: 'none', border: 'none',
                                        borderRadius: 'var(--radius-xs)',
                                        color: item.danger ? 'var(--danger)' : 'var(--ink-0)',
                                        cursor: 'pointer', textAlign: 'left',
                                        fontSize: '0.8rem', fontWeight: 500,
                                        fontFamily: 'var(--font-sans)',
                                        transition: 'background 0.15s ease',
                                      }}
                                      onMouseEnter={e => { e.currentTarget.style.background = item.danger ? 'var(--danger-soft)' : 'var(--paper-hover)'; }}
                                      onMouseLeave={e => { e.currentTarget.style.background = 'none'; }}
                                    >
                                      <item.icon style={{ width: 14, height: 14, flexShrink: 0 }} />
                                      {item.label}
                                    </button>
                                  ))}
                                </div>
                              )}
                            </div>
                          </div>
                        </div>
                        <div style={{ fontSize: '0.66rem', color: 'var(--ink-2)', marginBottom: '0.4rem', textTransform: 'uppercase', letterSpacing: '0.07em' }}>
                          Columns
                          <span style={{ marginLeft: '0.35rem', color: 'var(--ink-2)', fontWeight: 400, textTransform: 'none', letterSpacing: 0 }}>
                            ({t.columns.length})
                          </span>
                        </div>
                        {(() => {
                          const isExpanded = expandedColumns.has(idx);
                          const visibleCols = isExpanded ? t.columns : t.columns.slice(0, COLUMNS_PREVIEW_LIMIT);
                          const hasMore = t.columns.length > COLUMNS_PREVIEW_LIMIT;
                          return (
                            <>
                              <div
                                style={{
                                  display: 'flex',
                                  flexWrap: 'wrap',
                                  gap: '0.3rem',
                                  ...(isExpanded ? {
                                    maxHeight: '120px',
                                    overflowY: 'auto',
                                    paddingRight: '2px',
                                    scrollbarWidth: 'thin',
                                  } : {}),
                                }}
                              >
                                {visibleCols.map((col, cIdx) => (
                                  <span key={cIdx} style={{
                                    background: 'var(--paper-inset)',
                                    border: '1px solid var(--line)',
                                    borderRadius: '4px',
                                    padding: '0.1rem 0.4rem',
                                    fontSize: '0.68rem',
                                    color: 'var(--ink-1)',
                                    fontFamily: 'var(--font-mono)',
                                    maxWidth: '100%',
                                    overflow: 'hidden',
                                    textOverflow: 'ellipsis',
                                    whiteSpace: 'nowrap',
                                  }}>{col}</span>
                                ))}
                              </div>
                              {hasMore && (
                                <button
                                  onClick={() => toggleColumnExpansion(idx)}
                                  style={{
                                    marginTop: '0.4rem',
                                    background: 'none',
                                    border: 'none',
                                    cursor: 'pointer',
                                    color: 'var(--accent-strong)',
                                    fontSize: '0.68rem',
                                    fontWeight: 600,
                                    padding: 0,
                                    fontFamily: 'var(--font-sans)',
                                  }}
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
          </section>
        </aside>

        {/* Right Workspace */}
        <div style={{ display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          {viewingFile ? (
            <>
              {/* File viewer — replaces the editor and results panes */}
              <div style={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                gap: '0.75rem',
                padding: '0.7rem 1.5rem',
                background: 'var(--paper-1)',
                borderBottom: '1px solid var(--line)',
              }}>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.7rem', minWidth: 0 }}>
                  <span style={sectionLabelStyle}>File contents</span>
                  <span style={{
                    fontFamily: 'var(--font-mono)', fontSize: '0.85rem', fontWeight: 600,
                    color: 'var(--ink-0)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  }}>
                    {viewingFile}
                  </span>
                  {viewData?.source_format && (
                    <span style={{
                      fontFamily: 'var(--font-mono)', fontSize: '0.66rem', fontWeight: 600,
                      color: 'var(--accent-strong)', background: 'var(--accent-soft)',
                      border: '1px solid var(--accent-line)', borderRadius: '9999px',
                      padding: '0.08rem 0.5rem', textTransform: 'uppercase', letterSpacing: '0.05em',
                    }}>
                      {viewData.source_format}
                    </span>
                  )}
                  {viewData && (
                    <span style={{ fontSize: '0.72rem', color: 'var(--ink-2)', fontFamily: 'var(--font-mono)' }}>
                      {viewData.results.length.toLocaleString()} rows
                    </span>
                  )}
                </div>
                <button
                  type="button"
                  onClick={closeFileView}
                  aria-label="Close file view and return to the SQL editor"
                  style={{
                    display: 'inline-flex', alignItems: 'center', gap: '0.4rem', flexShrink: 0,
                    height: 32, padding: '0 0.8rem',
                    background: 'var(--paper-2)', color: 'var(--ink-1)',
                    border: '1px solid var(--line)', borderRadius: 'var(--radius-sm)',
                    cursor: 'pointer', fontSize: '0.8rem', fontWeight: 600,
                    transition: 'border-color 0.15s ease, color 0.15s ease',
                  }}
                  onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--accent-line)'; e.currentTarget.style.color = 'var(--ink-0)'; }}
                  onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--line)'; e.currentTarget.style.color = 'var(--ink-1)'; }}
                >
                  <X style={{ width: 14, height: 14 }} />
                  Back to editor
                </button>
              </div>

              <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
                {viewLoading && (
                  <div style={{
                    flex: 1, display: 'flex', flexDirection: 'column',
                    alignItems: 'center', justifyContent: 'center', gap: '0.8rem', color: 'var(--ink-2)',
                  }}>
                    <div className="loading-spinner-large" />
                    <span style={{ fontSize: '0.85rem' }}>Loading file contents…</span>
                  </div>
                )}

                {!viewLoading && viewError && (
                  <div style={{ padding: '1.5rem' }}>
                    <div className="message message-error">{viewError}</div>
                  </div>
                )}

                {!viewLoading && !viewError && viewData && (
                  viewData.source_format === 'json' ? (
                    <div style={{ flex: 1, minHeight: 0 }}>
                      <Editor
                        height="100%"
                        defaultLanguage="json"
                        theme="spymonkLight"
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
                          scrollbar: { verticalScrollbarSize: 8, horizontalScrollbarSize: 8 },
                          automaticLayout: true,
                        }}
                      />
                    </div>
                  ) : (
                    <div style={{ flex: 1, overflowY: 'auto', padding: '1.5rem' }}>
                      <div style={{
                        background: 'var(--paper-1)', border: '1px solid var(--line)',
                        borderRadius: 'var(--radius)', overflow: 'hidden', boxShadow: 'var(--shadow-sm)',
                      }}>
                        <DataTable
                          key={`view_${viewingFile}_${viewData.results.length}`}
                          columns={viewData.columns}
                          data={viewData.results}
                        />
                      </div>
                    </div>
                  )
                )}
              </div>
            </>
          ) : (
            <>

          {/* SQL Editor Header */}
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '0.7rem 1.5rem',
            background: 'var(--paper-1)',
            borderBottom: '1px solid var(--line)',
          }}>
            <span style={sectionLabelStyle}>SQL editor</span>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <button
                type="button"
                onClick={() => {
                  setIsAiPanelOpen(value => !value);
                  setAiError(null);
                }}
                aria-label="AI assistant"
                aria-pressed={isAiPanelOpen}
                title="AI assistant"
                style={{
                  display: 'inline-flex', alignItems: 'center', gap: '0.4rem',
                  height: 34, padding: '0 0.7rem',
                  background: isAiPanelOpen ? 'var(--accent-soft)' : 'var(--paper-2)',
                  color: isAiPanelOpen ? 'var(--accent-strong)' : 'var(--ink-1)',
                  border: `1px solid ${isAiPanelOpen ? 'var(--accent-line)' : 'var(--line)'}`,
                  borderRadius: 'var(--radius-sm)',
                  cursor: 'pointer', fontSize: '0.8rem', fontWeight: 600,
                  transition: 'background 0.15s ease, border-color 0.15s ease, color 0.15s ease',
                }}
              >
                <Sparkles style={{ width: 15, height: 15 }} />
                Assistant
              </button>

              {isExecuting && (
                <button
                  onClick={handleCancelQuery}
                  style={{
                    display: 'inline-flex', alignItems: 'center', gap: '0.4rem',
                    height: 34, padding: '0 0.9rem',
                    background: 'var(--danger-soft)',
                    color: 'var(--danger)',
                    border: '1px solid var(--danger-line)', borderRadius: 'var(--radius-sm)',
                    cursor: 'pointer', fontWeight: 600, fontSize: '0.82rem',
                  }}
                  aria-label="Cancel running query"
                >
                  <Square style={{ width: 13, height: 13 }} />
                  Cancel
                </button>
              )}

              <button
                onClick={handleRunQuery}
                disabled={!canRun}
                style={{
                  display: 'inline-flex', alignItems: 'center', gap: '0.45rem',
                  height: 34, padding: '0 1.05rem',
                  background: canRun ? 'var(--accent)' : 'var(--paper-inset)',
                  color: canRun ? '#fff' : 'var(--ink-2)',
                  border: 'none', borderRadius: 'var(--radius-sm)',
                  cursor: canRun ? 'pointer' : 'not-allowed',
                  fontWeight: 600, fontSize: '0.82rem',
                  transition: 'background 0.15s ease',
                }}
                onMouseEnter={e => { if (canRun) (e.currentTarget as HTMLButtonElement).style.background = 'var(--accent-hover)'; }}
                onMouseLeave={e => { if (canRun) (e.currentTarget as HTMLButtonElement).style.background = 'var(--accent)'; }}
                aria-label="Run SQL query"
                title="Run query  (⌘/Ctrl + Enter)"
              >
                {isExecuting ? (
                  <div className="loading-spinner" style={{ width: 14, height: 14, borderWidth: 2, borderTopColor: '#fff', borderColor: 'rgba(255,255,255,0.35)' }} />
                ) : (
                  <Play style={{ width: 14, height: 14 }} />
                )}
                {isExecuting ? 'Running…' : 'Run query'}
              </button>
            </div>
          </div>

          {isAiPanelOpen && (
            <section style={{
              background: 'var(--paper-1)',
              borderBottom: '1px solid var(--line)',
              padding: '1rem 1.5rem',
            }}>
              <div style={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                marginBottom: '0.75rem', flexWrap: 'wrap', gap: '0.5rem',
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                  <Sparkles style={{ width: 16, height: 16, color: 'var(--ink-1)' }} />
                  <span style={{ color: 'var(--ink-0)', fontSize: '1rem', fontWeight: 600, fontFamily: 'var(--font-display)' }}>
                    AI SQL assistant
                  </span>
                </div>
                <span style={{ color: 'var(--ink-2)', fontSize: '0.74rem' }}>
                  Optimize, generate, and fix — SELECT queries only.
                </span>
              </div>

              <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', marginBottom: '0.75rem' }}>
                {AI_MODES.map(mode => {
                  const active = aiMode === mode.id;
                  return (
                    <button
                      key={mode.id}
                      type="button"
                      aria-pressed={active}
                      onClick={() => {
                        setAiMode(mode.id);
                        setAiResult(null);
                        setAiError(null);
                      }}
                      style={{
                        display: 'inline-flex', alignItems: 'center', gap: '0.4rem',
                        padding: '0.4rem 0.8rem',
                        background: active ? 'var(--accent-soft)' : 'var(--paper-2)',
                        border: `1px solid ${active ? 'var(--accent-line)' : 'var(--line)'}`,
                        borderRadius: '999px',
                        color: active ? 'var(--accent-strong)' : 'var(--ink-1)',
                        cursor: 'pointer', fontSize: '0.76rem', fontWeight: 600,
                        transition: 'background 0.15s ease, border-color 0.15s ease, color 0.15s ease',
                      }}
                    >
                      {active && (
                        <span aria-hidden="true" style={{
                          width: 6, height: 6, borderRadius: '50%',
                          background: 'var(--accent-strong)', flexShrink: 0,
                        }} />
                      )}
                      {mode.label}
                    </button>
                  );
                })}
              </div>

              <textarea
                value={aiInput}
                onChange={(event) => setAiInput(event.target.value)}
                placeholder={AI_MODES.find(mode => mode.id === aiMode)?.placeholder}
                rows={3}
                style={{
                  width: '100%',
                  resize: 'vertical',
                  minHeight: 76,
                  background: 'var(--paper-2)',
                  border: '1px solid var(--line)',
                  borderRadius: 'var(--radius-sm)',
                  color: 'var(--ink-0)',
                  padding: '0.7rem 0.8rem',
                  fontSize: '0.86rem',
                  fontFamily: 'var(--font-sans)',
                  outline: 'none',
                }}
                aria-label="AI assistant prompt"
              />

              {aiMode === 'fix_sql_error' && lastQueryError && (
                <p style={{ color: 'var(--danger)', fontSize: '0.75rem', marginTop: '0.5rem' }}>
                  Last error: {lastQueryError}
                </p>
              )}

              <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', marginTop: '0.75rem' }}>
                <button
                  type="button"
                  onClick={handleSubmitAiRequest}
                  disabled={isAiLoading}
                  style={{
                    display: 'inline-flex', alignItems: 'center', gap: '0.4rem',
                    padding: '0.45rem 1rem',
                    background: isAiLoading ? 'var(--paper-inset)' : 'var(--accent)',
                    color: isAiLoading ? 'var(--ink-2)' : '#fff',
                    border: 'none', borderRadius: 'var(--radius-sm)',
                    cursor: isAiLoading ? 'wait' : 'pointer',
                    fontWeight: 600, fontSize: '0.82rem',
                    transition: 'background 0.15s ease',
                  }}
                  onMouseEnter={e => { if (!isAiLoading) (e.currentTarget as HTMLButtonElement).style.background = 'var(--accent-hover)'; }}
                  onMouseLeave={e => { if (!isAiLoading) (e.currentTarget as HTMLButtonElement).style.background = 'var(--accent)'; }}
                >
                  <Sparkles style={{ width: 14, height: 14 }} />
                  {isAiLoading ? 'Resolving…' : 'Resolve'}
                </button>

                {aiResult && (
                  <button
                    type="button"
                    onClick={handleApplyAiQuery}
                    style={{
                      padding: '0.45rem 1rem',
                      background: 'var(--sage-soft)',
                      border: '1px solid var(--sage-line)',
                      borderRadius: 'var(--radius-sm)',
                      color: 'var(--sage)',
                      cursor: 'pointer', fontWeight: 600, fontSize: '0.82rem',
                    }}
                  >
                    Apply to editor
                  </button>
                )}
              </div>

              {aiError && (
                <div className="message message-error" style={{ marginTop: '0.75rem' }}>
                  {aiError}
                </div>
              )}

              {aiResult && (
                <div style={{
                  marginTop: '0.75rem',
                  background: 'var(--paper-inset)',
                  border: '1px solid var(--line)',
                  borderRadius: 'var(--radius-sm)',
                  padding: '0.8rem',
                }}>
                  <div style={{ color: 'var(--ink-2)', fontSize: '0.68rem', fontWeight: 600, marginBottom: '0.5rem', textTransform: 'uppercase', letterSpacing: '0.07em' }}>
                    Suggested query
                  </div>
                  <pre style={{
                    margin: 0,
                    whiteSpace: 'pre-wrap',
                    color: 'var(--ink-0)',
                    fontSize: '0.82rem',
                    fontFamily: 'var(--font-mono)',
                  }}>
                    {aiResult.suggested_query}
                  </pre>
                  {aiResult.explanation && (
                    <p style={{ color: 'var(--ink-1)', fontSize: '0.82rem', margin: '0.7rem 0 0', lineHeight: 1.55 }}>
                      {aiResult.explanation}
                    </p>
                  )}
                </div>
              )}
            </section>
          )}

          {/* Monaco Editor */}
          <div style={{ height: '336px', borderBottom: '1px solid var(--line)', background: 'var(--paper-2)' }}>
            <Editor
              height="100%"
              defaultLanguage="sql"
              theme="spymonkLight"
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
                scrollbar: { verticalScrollbarSize: 8, horizontalScrollbarSize: 8 },
                automaticLayout: true,
                suggestOnTriggerCharacters: true,
                quickSuggestions: { other: true, comments: false, strings: true },
                wordBasedSuggestions: "off",
                lineNumbers: "on",
              }}
            />
          </div>

          {/* Results Area */}
          <div style={{ flex: 1, overflowY: 'auto', padding: '1.5rem' }}>
            {error && (
              <div style={{
                background: 'var(--danger-soft)', border: '1px solid var(--danger-line)',
                borderRadius: 'var(--radius-sm)', padding: '0.75rem 1rem',
                color: 'var(--danger)', fontSize: '0.86rem', marginBottom: '1rem',
                display: 'flex', alignItems: 'flex-start', gap: '0.5rem',
              }}>
                <Database style={{ width: 16, height: 16, flexShrink: 0, marginTop: 2 }} />
                <span><strong>Error:</strong> {error}</span>
              </div>
            )}

            {!error && queryResult && (
              <div className="rise-in">
                {/* Query Results Header */}
                <div style={{
                  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                  marginBottom: '0.75rem', flexWrap: 'wrap', gap: '0.6rem',
                }}>
                  <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.7rem' }}>
                    <span style={{ fontWeight: 600, color: 'var(--ink-0)', fontSize: '1.05rem', fontFamily: 'var(--font-display)' }}>Results</span>
                    <span style={{
                      fontFamily: 'var(--font-mono)', fontSize: '0.72rem', fontWeight: 600,
                      color: 'var(--ink-1)', background: 'var(--paper-inset)',
                      border: '1px solid var(--line)', borderRadius: '9999px',
                      padding: '0.15rem 0.6rem',
                    }}>
                      {queryResult.results.length.toLocaleString()} rows
                    </span>
                    {queryResult.cache_hit && (
                      <span style={{ fontSize: '0.72rem', color: 'var(--sage)', display: 'inline-flex', alignItems: 'center', gap: '0.3rem' }}>
                        ● cached
                      </span>
                    )}
                    {typeof queryResult.partitions_total === 'number' && queryResult.partitions_total > 0 && (
                      <span style={{ fontSize: '0.72rem', color: 'var(--ink-2)', fontFamily: 'var(--font-mono)' }}>
                        scanned {queryResult.partitions_scanned}/{queryResult.partitions_total} partitions
                      </span>
                    )}
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                    {(['csv', 'json', 'xlsx'] as DownloadFormat[]).map(format => (
                      <button
                        key={format}
                        type="button"
                        onClick={() => handleDownloadResults(format)}
                        aria-label={`Download query results as ${format.toUpperCase()}`}
                        title={`Download ${format.toUpperCase()}`}
                        style={{
                          display: 'inline-flex', alignItems: 'center', gap: '0.3rem',
                          padding: '0.28rem 0.6rem',
                          background: 'var(--paper-1)',
                          border: '1px solid var(--line)',
                          borderRadius: '6px',
                          color: 'var(--ink-1)',
                          cursor: 'pointer',
                          fontSize: '0.7rem',
                          fontWeight: 600,
                          textTransform: 'uppercase',
                          letterSpacing: '0.07em',
                          transition: 'background 0.15s ease, border-color 0.15s ease, color 0.15s ease',
                        }}
                        onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--accent-line)'; e.currentTarget.style.color = 'var(--accent-strong)'; }}
                        onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--line)'; e.currentTarget.style.color = 'var(--ink-1)'; }}
                      >
                        <Download style={{ width: 12, height: 12 }} />
                        {format}
                      </button>
                    ))}
                  </div>
                </div>

                <div style={{
                  background: 'var(--paper-1)', border: '1px solid var(--line)',
                  borderRadius: 'var(--radius)', overflow: 'hidden', boxShadow: 'var(--shadow-sm)',
                }}>
                  <DataTable
                    key={`${queryResult.table_used}_${queryResult.results.length}`}
                    columns={queryResult.columns}
                    data={queryResult.results}
                  />
                </div>
              </div>
            )}

            {!error && !queryResult && !isExecuting && (
              <div style={{
                height: '100%', display: 'flex', flexDirection: 'column',
                alignItems: 'center', justifyContent: 'center',
                color: 'var(--ink-2)', textAlign: 'center',
              }}>
                <div style={{
                  width: 56, height: 56, borderRadius: '14px',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  background: 'var(--paper-1)', border: '1px solid var(--line)',
                  marginBottom: '1rem',
                }}>
                  <Database style={{ width: 24, height: 24, color: 'var(--ink-2)' }} />
                </div>
                <p style={{ fontSize: '1.05rem', fontFamily: 'var(--font-display)', color: 'var(--ink-1)', marginBottom: '0.3rem' }}>
                  Ready when you are
                </p>
                <p style={{ fontSize: '0.85rem', color: 'var(--ink-2)' }}>
                  Write a query and press <strong style={{ color: 'var(--accent-strong)' }}>Run query</strong> — or ⌘/Ctrl + Enter.
                </p>
              </div>
            )}
          </div>
            </>
          )}
        </div>
      </div>

      {/* Table details modal */}
      {detailsFor && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label={`Details for table ${detailsFor}`}
          style={{
            position: 'fixed', inset: 0, zIndex: 50,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            padding: '1rem',
            background: 'rgba(46, 53, 71, 0.35)',
          }}
          onMouseDown={e => { if (e.target === e.currentTarget) closeDetails(); }}
        >
          <div style={{
            width: 'min(600px, 100%)', maxHeight: 'min(82vh, 720px)',
            display: 'flex', flexDirection: 'column',
            background: 'var(--paper-1)',
            border: '1px solid var(--line)',
            borderRadius: 'var(--radius)',
            boxShadow: 'var(--shadow-lg)',
            overflow: 'hidden',
          }}>
            <div style={{
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              padding: '0.9rem 1.25rem',
              borderBottom: '1px solid var(--line)', flexShrink: 0,
            }}>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.6rem', minWidth: 0 }}>
                <span style={{ fontFamily: 'var(--font-display)', fontSize: '1.1rem', fontWeight: 600, color: 'var(--ink-0)' }}>
                  Table details
                </span>
                <span style={{
                  fontFamily: 'var(--font-mono)', fontSize: '0.85rem', color: 'var(--ink-1)',
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }}>
                  {detailsFor}
                </span>
              </div>
              <button
                type="button"
                onClick={closeDetails}
                aria-label="Close details"
                className="btn-icon"
              >
                <X style={{ width: 15, height: 15 }} />
              </button>
            </div>

            <div style={{ overflowY: 'auto', padding: '1.1rem 1.25rem', display: 'flex', flexDirection: 'column', gap: '1.15rem' }}>
              {detailsLoading && (
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '0.8rem', padding: '2rem 0', color: 'var(--ink-2)' }}>
                  <div className="loading-spinner-large" />
                  <span style={{ fontSize: '0.85rem' }}>Loading details…</span>
                </div>
              )}

              {detailsError && !detailsLoading && (
                <div className="message message-error">{detailsError}</div>
              )}

              {details && !detailsLoading && (
                <>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: '0.6rem' }}>
                    {[
                      { label: 'Total rows', value: details.record_count.toLocaleString() },
                      { label: 'Columns', value: String(details.column_count) },
                      { label: 'Format', value: details.source_format ? details.source_format.toUpperCase() : '—' },
                      { label: 'Uploaded', value: details.uploaded_at ? new Date(details.uploaded_at).toLocaleString() : '—' },
                    ].map(stat => (
                      <div key={stat.label} style={{
                        background: 'var(--paper-inset)', border: '1px solid var(--line)',
                        borderRadius: 'var(--radius-sm)', padding: '0.55rem 0.7rem',
                      }}>
                        <div style={{ fontSize: '0.64rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.07em', color: 'var(--ink-2)', marginBottom: '0.2rem' }}>
                          {stat.label}
                        </div>
                        <div style={{ fontSize: '0.85rem', fontWeight: 600, color: 'var(--ink-0)', fontFamily: 'var(--font-mono)' }}>
                          {stat.value}
                        </div>
                      </div>
                    ))}
                  </div>

                  <section>
                    <div style={{ ...sectionLabelStyle, marginBottom: '0.5rem' }}>Table definitions</div>
                    <div style={{ border: '1px solid var(--line)', borderRadius: 'var(--radius-sm)', overflow: 'hidden' }}>
                      {details.columns.map((col, i) => (
                        <div key={col} style={{
                          display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '0.75rem',
                          padding: '0.42rem 0.7rem',
                          borderTop: i === 0 ? 'none' : '1px solid var(--line-soft)',
                          background: i % 2 ? 'var(--paper-inset)' : 'var(--paper-1)',
                        }}>
                          <span style={{ fontFamily: 'var(--font-mono)', fontSize: '0.8rem', color: 'var(--ink-0)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {col}
                          </span>
                          <span style={{ fontFamily: 'var(--font-mono)', fontSize: '0.72rem', color: 'var(--ink-2)', flexShrink: 0 }}>
                            {details.schema?.[col] ?? '—'}
                          </span>
                        </div>
                      ))}
                    </div>
                  </section>

                  <section>
                    <div style={{ ...sectionLabelStyle, marginBottom: '0.5rem' }}>Last 5 queries</div>
                    {details.last_queries.length === 0 ? (
                      <p style={{ fontSize: '0.8rem', color: 'var(--ink-2)' }}>
                        No queries have been run on this table yet.
                      </p>
                    ) : (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                        {details.last_queries.map((q, i) => (
                          <div key={i} style={{
                            background: 'var(--paper-inset)', border: '1px solid var(--line)',
                            borderRadius: 'var(--radius-sm)', padding: '0.55rem 0.7rem',
                          }}>
                            <pre style={{
                              margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                              fontFamily: 'var(--font-mono)', fontSize: '0.76rem', color: 'var(--ink-0)', lineHeight: 1.5,
                            }}>
                              {q.query}
                            </pre>
                            <div style={{ marginTop: '0.35rem', fontSize: '0.68rem', color: 'var(--ink-2)', display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
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
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
