import { forwardRef, type ButtonHTMLAttributes, type PropsWithChildren } from 'react'
import { buttonClassName, type ButtonVariant } from './buttonClassName'

export type ButtonProps = PropsWithChildren<
  ButtonHTMLAttributes<HTMLButtonElement> & {
    variant?: ButtonVariant
  }
>

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'primary', className, children, type = 'button', ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      className={buttonClassName(variant, className)}
      {...props}
    >
      {children}
    </button>
  )
})
