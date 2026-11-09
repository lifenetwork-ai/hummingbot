import type { ButtonHTMLAttributes, PropsWithChildren } from 'react'
import { buttonClassName, type ButtonVariant } from './buttonClassName'

type ButtonProps = PropsWithChildren<
  ButtonHTMLAttributes<HTMLButtonElement> & {
    variant?: ButtonVariant
  }
>

export function Button({
  variant = 'primary',
  className,
  children,
  type = 'button',
  ...props
}: ButtonProps) {
  return (
    <button
      type={type}
      className={buttonClassName(variant, className)}
      {...props}
    >
      {children}
    </button>
  )
}
