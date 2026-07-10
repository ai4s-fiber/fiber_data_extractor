/** Trigger a browser download from an API blob response. */

export function downloadBlobResponse(
  data: BlobPart,
  filename: string,
  mimeType = 'application/octet-stream',
) {
  const url = window.URL.createObjectURL(new Blob([data], { type: mimeType }));
  const link = document.createElement('a');
  link.href = url;
  link.setAttribute('download', filename);
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(url);
}
