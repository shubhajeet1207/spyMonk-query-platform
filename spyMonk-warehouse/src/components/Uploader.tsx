import React, { useCallback, useState } from 'react';
import { useDropzone } from 'react-dropzone';
import { UploadCloud, FileType, AlertCircle } from 'lucide-react';
import { API_BASE_URL, getAuthHeaders } from '../config/api';

interface TableInfo {
  table_name: string;
  record_count: number;
  columns: string[];
}

interface UploaderProps {
  onUploadSuccess: (tableInfo: TableInfo) => void;
}

const Uploader: React.FC<UploaderProps> = ({ onUploadSuccess }) => {
  const [isUploading, setIsUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onDrop = useCallback(async (acceptedFiles: File[]) => {
    if (acceptedFiles.length === 0) return;
    
    const file = acceptedFiles[0];
    setIsUploading(true);
    setError(null);

    const formData = new FormData();
    formData.append('file', file);

    try {
      const response = await fetch(`${API_BASE_URL}/upload`, {
        method: 'POST',
        headers: getAuthHeaders(),
        body: formData,
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Upload failed');
      }

      const data: TableInfo = await response.json();
      onUploadSuccess(data);
    } catch (err: unknown) {
      const errorMessage = err instanceof Error ? err.message : 'An unexpected error occurred';
      setError(errorMessage);
    } finally {
      setIsUploading(false);
    }
  }, [onUploadSuccess]);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: {
      'text/csv': ['.csv'],
      'application/json': ['.json'],
      'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': ['.xlsx']
    },
    maxFiles: 1
  });

  return (
    <div className="w-full">
      <div
        {...getRootProps()}
        style={{
          padding: '1.5rem',
          border: `1.5px dashed ${isDragActive ? 'var(--accent)' : 'var(--line)'}`,
          borderRadius: 'var(--radius-sm)',
          background: isDragActive ? 'var(--accent-soft)' : 'var(--paper-2)',
          transition: 'border-color 0.15s ease, background 0.15s ease',
          cursor: 'pointer',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: '0.75rem'
        }}
        onMouseEnter={(e) => {
          if (!isDragActive) e.currentTarget.style.borderColor = 'var(--accent-line)';
        }}
        onMouseLeave={(e) => {
          if (!isDragActive) e.currentTarget.style.borderColor = 'var(--line)';
        }}
      >
        <input {...getInputProps()} />

        {isUploading ? (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '0.75rem' }}>
            <div className="loading-spinner-large" />
            <p style={{ color: 'var(--ink-1)', fontWeight: 500 }}>Processing…</p>
          </div>
        ) : (
          <>
            <div style={{
              padding: '0.7rem',
              background: 'var(--paper-1)',
              borderRadius: 'var(--radius-sm)',
              border: '1px solid var(--line)'
            }}>
              <UploadCloud
                className="w-8 h-8"
                style={{ color: 'var(--accent-strong)' }}
              />
            </div>
            <div style={{ textAlign: 'center' }}>
              <p style={{
                fontSize: '0.85rem',
                fontWeight: 600,
                color: 'var(--ink-0)',
                marginBottom: '0.35rem',
              }}>
                {isDragActive ? "Drop your file to upload" : "Drag a file here, or click to browse"}
              </p>
              <p style={{
                fontSize: '0.7rem',
                color: 'var(--ink-2)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: '0.4rem',
              }} className="font-mono">
                <FileType className="w-3 h-3" /> csv · json · xlsx
              </p>
            </div>
          </>
        )}
      </div>

      {error && (
        <div className="message message-error" style={{ marginTop: '1rem' }}>
          <AlertCircle className="w-5 h-5" />
          <p>{error}</p>
        </div>
      )}
    </div>
  );
};

export default Uploader;
