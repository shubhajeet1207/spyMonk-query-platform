import { useState } from 'react';
import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from 'lucide-react';

interface DataTableProps {
  columns: string[];
  data: Record<string, unknown>[];
}

const PAGE_SIZE = 20;
const MONO = "'JetBrains Mono', ui-monospace, monospace";

const DataTable: React.FC<DataTableProps> = ({ columns, data }) => {
  const [currentPage, setCurrentPage] = useState(1);

  if (!columns || columns.length === 0 || !data || data.length === 0) {
    return (
      <div className="empty-state">
        <p className="empty-state-title">No data</p>
        <p className="empty-state-description">Run a query to see results here.</p>
      </div>
    );
  }

  const totalPages = Math.ceil(data.length / PAGE_SIZE);
  const startIndex = (currentPage - 1) * PAGE_SIZE;
  const paginatedData = data.slice(startIndex, startIndex + PAGE_SIZE);

  const goToPage = (page: number) => {
    const pageNumber = Math.max(1, Math.min(page, totalPages));
    setCurrentPage(pageNumber);
  };

  return (
    <div style={{ width: '100%', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
      <div style={{ width: '100%', overflowX: 'auto', flex: 1 }}>
        <table style={{
          width: '100%',
          textAlign: 'left',
          fontSize: '0.875rem',
          borderCollapse: 'collapse'
        }}>
          <thead style={{
            background: 'var(--paper-inset)',
            position: 'sticky',
            top: 0,
            zIndex: 10
          }}>
            <tr>
              {columns.map((col, index) => (
                <th
                  key={index}
                  style={{
                    padding: '0.6rem 0.85rem',
                    fontWeight: 600,
                    color: 'var(--ink-1)',
                    borderBottom: '1px solid var(--line)',
                    textTransform: 'uppercase',
                    fontSize: '0.68rem',
                    letterSpacing: '0.07em',
                    whiteSpace: 'nowrap',
                    fontFamily: MONO
                  }}
                >
                  {col}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {paginatedData.map((row, rowIndex) => (
              <tr
                key={rowIndex}
                style={{
                  borderBottom: '1px solid var(--line-soft)',
                  transition: 'background 0.15s ease'
                }}
                onMouseEnter={(e) => e.currentTarget.style.background = 'var(--paper-hover)'}
                onMouseLeave={(e) => e.currentTarget.style.background = 'transparent'}
              >
                {columns.map((col, colIndex) => (
                  <td
                    key={colIndex}
                    style={{
                      padding: '0.5rem 0.85rem',
                      color: 'var(--ink-0)',
                      fontFamily: MONO,
                      fontSize: '0.8rem',
                      whiteSpace: 'nowrap'
                    }}
                  >
                    {row[col] !== null && row[col] !== undefined ? String(row[col]) : (
                      <span style={{ color: 'var(--ink-1)', fontStyle: 'italic' }}>null</span>
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination Controls */}
      <div style={{
        padding: '0.7rem 1rem',
        background: 'var(--paper-1)',
        borderTop: '1px solid var(--line)',
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center'
      }}>
        <div style={{ fontSize: '0.75rem', color: 'var(--ink-1)' }}>
          Showing <strong style={{ color: 'var(--ink-0)' }}>{startIndex + 1}</strong> to <strong style={{ color: 'var(--ink-0)' }}>{Math.min(startIndex + PAGE_SIZE, data.length)}</strong> of <strong style={{ color: 'var(--ink-0)' }}>{data.length.toLocaleString()}</strong> rows
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <button
            onClick={() => goToPage(1)}
            disabled={currentPage === 1}
            className="btn-icon"
            aria-label="First page"
            title="First page"
          >
            <ChevronsLeft className="w-4 h-4" />
          </button>
          <button
            onClick={() => goToPage(currentPage - 1)}
            disabled={currentPage === 1}
            className="btn-icon"
            aria-label="Previous page"
            title="Previous page"
          >
            <ChevronLeft className="w-4 h-4" />
          </button>

          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: '0.4rem',
            margin: '0 0.4rem',
            fontSize: '0.75rem'
          }}>
            <span style={{ color: 'var(--ink-1)' }}>Page</span>
            <span style={{
              fontFamily: MONO,
              fontWeight: 600,
              color: 'var(--ink-0)',
              background: 'var(--paper-inset)',
              border: '1px solid var(--line)',
              borderRadius: '6px',
              padding: '0.1rem 0.5rem',
            }}>
              {currentPage}
            </span>
            <span style={{ color: 'var(--ink-1)' }}>of {totalPages}</span>
          </div>

          <button
            onClick={() => goToPage(currentPage + 1)}
            disabled={currentPage === totalPages}
            className="btn-icon"
            aria-label="Next page"
            title="Next page"
          >
            <ChevronRight className="w-4 h-4" />
          </button>
          <button
            onClick={() => goToPage(totalPages)}
            disabled={currentPage === totalPages}
            className="btn-icon"
            aria-label="Last page"
            title="Last page"
          >
            <ChevronsRight className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  );
};

export default DataTable;
