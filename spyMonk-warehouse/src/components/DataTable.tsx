import { useState } from 'react';
import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from 'lucide-react';

interface DataTableProps {
  columns: string[];
  data: Record<string, unknown>[];
}

const PAGE_SIZE = 20;

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
    <div className="dt">
      <div className="dt-scroll">
        <table>
          <thead>
            <tr>
              {columns.map((col, index) => (
                <th key={index}>{col}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {paginatedData.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {columns.map((col, colIndex) => (
                  <td key={colIndex}>
                    {row[col] !== null && row[col] !== undefined ? String(row[col]) : (
                      <span className="dt-null">null</span>
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination: one joined row of square buttons, like the boards' media controls. */}
      <div className="dt-footer">
        <div className="dt-count">
          Showing <strong>{startIndex + 1}</strong> to <strong>{Math.min(startIndex + PAGE_SIZE, data.length)}</strong> of <strong>{data.length.toLocaleString()}</strong> rows
        </div>

        <div className="dt-pager">
          <div className="btn-group">
            <button
              onClick={() => goToPage(1)}
              disabled={currentPage === 1}
              className="btn-icon"
              aria-label="First page"
              title="First page"
            >
              <ChevronsLeft />
            </button>
            <button
              onClick={() => goToPage(currentPage - 1)}
              disabled={currentPage === 1}
              className="btn-icon"
              aria-label="Previous page"
              title="Previous page"
            >
              <ChevronLeft />
            </button>
          </div>

          <div className="dt-page">
            <span>Page</span>
            <b>{currentPage}</b>
            <span>of {totalPages}</span>
          </div>

          <div className="btn-group">
            <button
              onClick={() => goToPage(currentPage + 1)}
              disabled={currentPage === totalPages}
              className="btn-icon"
              aria-label="Next page"
              title="Next page"
            >
              <ChevronRight />
            </button>
            <button
              onClick={() => goToPage(totalPages)}
              disabled={currentPage === totalPages}
              className="btn-icon"
              aria-label="Last page"
              title="Last page"
            >
              <ChevronsRight />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default DataTable;
