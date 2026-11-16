import { parseAmount } from './parseAmount'

/**
 * Định dạng giá (entry / mark / liquidation) cho hiển thị: giữ 5 chữ số có nghĩa,
 * thêm phân cách hàng nghìn, bỏ số 0 thừa ở cuối (vd "0.100000" → "0.1").
 * TODO(api): khi backend trả Decimal string thô thì thay parseAmount bằng parser decimal chuẩn.
 */
export function formatPriceForDisplay(value: string | null | undefined): string | null {
  const n = parseAmount(value)
  if (n === null) return null
  return n.toLocaleString('en-US', { maximumSignificantDigits: 5 })
}

/**
 * Định dạng số lượng token (amount / inventory limit): cùng quy tắc 5 chữ số có nghĩa
 * với giá — giữ nguyên độ chính xác tương đối cho cả số lớn lẫn số lẻ thập phân.
 */
export const formatQuantityForDisplay = formatPriceForDisplay

/**
 * Định dạng số USDT (PnL / margin / phí) cho hiển thị: làm tròn 2 chữ số thập phân,
 * phân cách hàng nghìn. `signed: true` giữ dấu + cho số dương (PnL).
 */
export function formatUsdtForDisplay(value: string | null | undefined, signed = false): string | null {
  const n = parseAmount(value)
  if (n === null) return null
  const formatted = Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  return `${n < 0 ? '-' : signed ? '+' : ''}${formatted}`
}
