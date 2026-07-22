import NextAuth from "next-auth";
import Credentials from "next-auth/providers/credentials";
import { compare } from "bcryptjs";

declare module "next-auth" {
  interface User {
    deviceFingerprint?: string;
  }
}

export const { handlers, auth, signIn, signOut } = NextAuth({
  providers: [
    Credentials({
      name: "password",
      credentials: {
        username: { label: "用户名", type: "text" },
        password: { label: "密码", type: "password" },
        deviceFingerprint: { label: "设备指纹", type: "text" },
      },
      authorize: async (credentials) => {
        const validUser =
          credentials.username ===
          (process.env.DASHBOARD_USER || "admin");
        if (!validUser) return null;

        const hash = process.env.DASHBOARD_PASSWORD_HASH;
        if (!hash) {
          console.error("[AUTH] DASHBOARD_PASSWORD_HASH not set");
          return null;
        }

        const validPass = await compare(
          credentials.password as string,
          hash
        );
        if (!validPass) return null;

        return {
          id: "1",
          name: "admin",
          deviceFingerprint: (credentials.deviceFingerprint as string) || "",
        };
      },
    }),
  ],
  session: { strategy: "jwt" },
  callbacks: {
    jwt: async ({ token, user }) => {
      if (user) {
        (token as any).deviceFingerprint = user.deviceFingerprint;
      }
      return token;
    },
    session: async ({ session, token }) => {
      if (session.user) {
        (session.user as any).deviceFingerprint = (token as any).deviceFingerprint;
      }
      return session;
    },
  },
  cookies: {
    sessionToken: {
      name: "__hermes_session",
      options: {
        httpOnly: true,
        secure: process.env.NODE_ENV === "production",
        sameSite: "lax",
        path: "/",
      },
    },
  },
  pages: {
    signIn: "/login",
    error: "/login",
  },
  trustHost: true,
});
