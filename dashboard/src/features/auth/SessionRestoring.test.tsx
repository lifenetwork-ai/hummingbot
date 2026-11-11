import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { SessionRestoring } from './SessionRestoring'

describe('SessionRestoring', () => {
  it('renders status role with accessible message for session restoration', () => {
    render(<SessionRestoring />)

    const statusRegion = screen.getByRole('status')
    expect(statusRegion).toBeInTheDocument()
    expect(statusRegion).toHaveTextContent('Đang khôi phục phiên đăng nhập')
    expect(statusRegion).toHaveTextContent('LIFE · Market making')
    expect(statusRegion).toHaveTextContent('Đang kiểm tra thông tin đăng nhập đã lưu và xác minh quyền truy cập không gian làm việc')
    expect(statusRegion).toHaveTextContent('Đang xác minh phiên làm việc với cổng truy cập quản trị')
  })

  it('renders the brand logo and heading', () => {
    render(<SessionRestoring />)

    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Đang khôi phục phiên đăng nhập…')
    expect(screen.getByAltText('LIFE AI')).toBeInTheDocument()
  })
})
