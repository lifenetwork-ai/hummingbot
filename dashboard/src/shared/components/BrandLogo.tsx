import { cn } from '../utils/cn'

type BrandLogoProps = {
  className?: string
}

export function BrandLogo({ className }: BrandLogoProps) {
  return (
    <span className={cn('inline-flex items-center', className)}>
      <img
        src="/brand/lifeai-logo.svg"
        alt="LIFE AI"
        className="h-7 w-auto max-w-[130px] object-contain dark:hidden"
      />
      <img
        src="/brand/lifeai-logo-white.svg"
        alt=""
        aria-hidden="true"
        className="hidden h-7 w-auto max-w-[130px] object-contain dark:block"
      />
    </span>
  )
}
