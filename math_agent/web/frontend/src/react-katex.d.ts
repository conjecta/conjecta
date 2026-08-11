declare module 'react-katex' {
  import type { ReactNode } from 'react';

  interface MathComponentProps {
    math?: string;
    children?: ReactNode;
    errorColor?: string;
    renderError?: (error: Error) => ReactNode;
  }

  export function BlockMath(props: MathComponentProps): JSX.Element;
  export function InlineMath(props: MathComponentProps): JSX.Element;
}
