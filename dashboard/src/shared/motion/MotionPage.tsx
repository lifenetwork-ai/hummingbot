import { motion, useReducedMotion } from 'framer-motion'
import { cn } from '../utils/cn'

export interface MotionPageProps {
  children?: React.ReactNode
  className?: string
}

export function MotionPage({ children, className }: MotionPageProps) {
  const reducedMotion = useReducedMotion()

  return (
    <motion.div
      initial={reducedMotion ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{
        duration: reducedMotion ? 0 : 0.3,
        ease: [0.16, 1, 0.3, 1],
      }}
      className={cn('flex flex-1 flex-col w-full', className)}
    >
      {children}
    </motion.div>
  )
}
