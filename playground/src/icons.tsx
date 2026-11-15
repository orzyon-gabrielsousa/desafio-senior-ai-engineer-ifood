import type { SVGProps } from "react";

function Icon({ children, ...props }: SVGProps<SVGSVGElement>) {
  return (
    <svg
      viewBox="0 0 24 24"
      width="20"
      height="20"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      {children}
    </svg>
  );
}

export function MenuIcon() {
  return (
    <Icon>
      <path d="M4 7h16M4 12h16M4 17h16" />
    </Icon>
  );
}

export function PlusIcon() {
  return (
    <Icon strokeWidth="2.4">
      <path d="M12 5v14M5 12h14" />
    </Icon>
  );
}

export function CloseIcon() {
  return (
    <Icon strokeWidth="2.2">
      <path d="M6 6l12 12M18 6 6 18" />
    </Icon>
  );
}

export function CheckIcon() {
  return (
    <Icon strokeWidth="2.8">
      <path d="m5 12.5 4.2 4.2L19 7" />
    </Icon>
  );
}

export function ChatIcon() {
  return (
    <Icon strokeWidth="1.8">
      <path d="M20 15a2 2 0 0 1-2 2H8l-4 4V6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2z" />
    </Icon>
  );
}

export function CartIcon() {
  return (
    <Icon strokeWidth="1.9">
      <path d="M3 4h2.2l1.6 10.2a1.5 1.5 0 0 0 1.5 1.3h8.6a1.5 1.5 0 0 0 1.5-1.1L20 8H6" />
      <circle cx="9.5" cy="19.2" r="1.2" fill="currentColor" stroke="none" />
      <circle cx="16.5" cy="19.2" r="1.2" fill="currentColor" stroke="none" />
    </Icon>
  );
}

export function StoreIcon() {
  return (
    <Icon strokeWidth="1.8">
      <path d="M4 9.5 5.5 4h13L20 9.5M4 9.5a2.5 2.5 0 0 0 5 0 2.5 2.5 0 0 0 5 0 2.5 2.5 0 0 0 5 0M5.5 12.5V20h13v-7.5M10 20v-4.5h4V20" />
    </Icon>
  );
}

export function ChartIcon() {
  return (
    <Icon strokeWidth="2.2">
      <path d="M3.5 20.5h17M7 20V13M12 20V5M17 20v-9" />
    </Icon>
  );
}

export function BookmarkIcon({ filled = false }: { filled?: boolean }) {
  return (
    <Icon strokeWidth="1.9" fill={filled ? "currentColor" : "none"}>
      <path d="M7 4h10a1 1 0 0 1 1 1v15l-6-4-6 4V5a1 1 0 0 1 1-1z" />
    </Icon>
  );
}

export function SparkIcon() {
  return (
    <Icon fill="currentColor" stroke="none">
      <path d="M12 2.5 14 9l6.5 2L14 13l-2 6.5L10 13l-6.5-2L10 9zM19 15.5l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z" />
    </Icon>
  );
}

export function TrashIcon() {
  return (
    <Icon strokeWidth="1.8">
      <path d="M4 7h16M9.5 7V4.5h5V7M6.5 7l.8 12.5h9.4L17.5 7M10 11v5M14 11v5" />
    </Icon>
  );
}

export function MinusIcon() {
  return (
    <Icon strokeWidth="2.4">
      <path d="M5 12h14" />
    </Icon>
  );
}

export function SendIcon() {
  return (
    <Icon fill="currentColor" stroke="none">
      <path d="M2.01 21 23 12 2.01 3 2 10l15 2-15 2z" />
    </Icon>
  );
}

export function StopIcon() {
  return (
    <Icon fill="currentColor" stroke="none">
      <rect x="6.5" y="6.5" width="11" height="11" rx="2.5" />
    </Icon>
  );
}
