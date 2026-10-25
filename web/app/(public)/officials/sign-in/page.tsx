import { OfficerSignIn } from "@/components/auth/officer-sign-in";
import { pageMetadata } from "@/lib/metadata";

export const metadata = pageMetadata({
  title: "Officer sign-in",
  description: "Sign in to your department desk at LandChain.",
  path: "/officials/sign-in",
  noindex: true,
});

export default async function OfficerSignInPage({ searchParams }: { searchParams: Promise<{ next?: string }> }) {
  const { next } = await searchParams;
  return (
    <div className="page">
      <header className="page-head">
        <h1>Officer sign-in</h1>
        <p>Your actions are signed with your own department certificate, not with a shared account.</p>
      </header>
      <OfficerSignIn next={next} />
    </div>
  );
}
