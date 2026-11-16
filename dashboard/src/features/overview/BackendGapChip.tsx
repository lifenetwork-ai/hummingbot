import { Info } from 'lucide-react'
import { Badge } from '../../shared/components/Badge'

export interface BackendGapChipProps {
  /** Mô tả hiển thị khi hover — nên ghi rõ field nào thiếu */
  detail: string
  /**
   * Nhãn chip. Mặc định "CHƯA CÓ DỮ LIỆU" (hiển thị — thay vì số 0/giả).
   * Dùng "MOCK" khi đang dùng giá trị mock để verify bố cục chờ backend cung cấp.
   */
  label?: string
  className?: string
}

/**
 * Chip thống nhất cho dữ liệu backend chưa cung cấp (OVERVIEW-REVIEW mục 5).
 * Dùng một chỗ duy nhất để khi backend bổ sung field chỉ cần xóa chip theo TODO(api).
 */
export function BackendGapChip({ detail, label = 'NO DATA', className }: BackendGapChipProps) {
  return (
    <span title={detail} className={className}>
      <Badge tone="neutral" className="gap-1 text-[9px] text-app-muted">
        <span>{label}</span>
        <Info size={10} className="shrink-0 opacity-70" aria-hidden="true" />
      </Badge>
    </span>
  )
}
