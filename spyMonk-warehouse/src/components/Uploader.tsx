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
      <div {...getRootProps({ className: isDragActive ? 'dropzone is-active' : 'dropzone' })}>
        <input {...getInputProps()} />

        {isUploading ? (
          <div role="status" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '0.7rem' }}>
            <div className="loader" aria-hidden="true"><span /></div>
            <p className="dropzone-title">Processing…</p>
          </div>
        ) : (
          <>
            <span className="dropzone-icon" aria-hidden="true">
              <UploadCloud />
            </span>
            <div>
              <p className="dropzone-title" style={{ marginBottom: '0.35rem' }}>
                {isDragActive ? "Drop your file to upload" : "Drag a file here, or click to browse"}
              </p>
              <p className="eyebrow" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '0.4rem' }}>
                <FileType className="w-3 h-3" aria-hidden="true" /> csv · json · xlsx
              </p>
            </div>
          </>
        )}
      </div>

      {error && (
        <div className="message message-error" role="alert" style={{ marginTop: '0.9rem' }}>
          <AlertCircle aria-hidden="true" />
          <p>{error}</p>
        </div>
      )}
    </div>
  );
};

export default Uploader;
