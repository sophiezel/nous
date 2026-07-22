// Force dynamic rendering so middleware CSP headers apply
export const dynamic = "force-dynamic";

export default function LoginLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
