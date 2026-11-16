/**
 * Parse chuỗi số hiển thị có dấu phẩy phân cách hàng nghìn / đơn vị
 * (vd "6,120", "412,000 LIFE", "+182.40") về number.
 * Mock hiện lưu số dạng chuỗi đã format — khi thay API thật (TODO(api): nhận Decimal string
 * thô từ backend, không qua format) có thể thay hàm này bằng parser decimal chuẩn của app.
 */
export function parseAmount(value: string | null | undefined): number | null {
  if (value === null || value === undefined) return null
  const n = Number.parseFloat(value.replace(/[^0-9.+-]/g, ''))
  return Number.isFinite(n) ? n : null
}
