import { cn } from '@/lib/utils';
import { InputHTMLAttributes, forwardRef } from 'react';

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function Input({ className, ...props }, ref) {
    return (
      <input
        ref={ref}
        {...props}
        className={cn(
          'h-11 w-full px-4 text-sm rounded-xl bg-white border border-ink-200',
          'placeholder:text-ink-400',
          'focus:outline-none focus:border-rouge-400 focus:ring-4 focus:ring-rouge-500/10',
          'transition-all duration-200',
          className,
        )}
      />
    );
  },
);
