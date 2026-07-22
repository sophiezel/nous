// Force dynamic rendering so middleware CSP headers apply
export const dynamic = "force-dynamic";

export default function ActivateLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
