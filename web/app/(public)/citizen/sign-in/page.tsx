import { CitizenSignIn } from "@/components/auth/citizen-sign-in";
import { pageMetadata } from "@/lib/metadata";

export const metadata = pageMetadata({
  title: "Citizen sign-in",
  description: "Sign in to see your land and sign transfers, with DigiLocker and the key kept in your browser.",
  path: "/citizen/sign-in",
  noindex: true,
});

export default async function Page({ searchParams }: { searchParams: Promise<{ next?: string }> }) {
  const { next } = await searchParams;
  return (
    <div className="page">
      <header className="page-head">
        <h1>Citizen sign-in</h1>
        <p>DigiLocker confirms who you are; the key in your browser confirms that it is you signing.</p>
      </header>
      <CitizenSignIn next={next} />
    </div>
  );
}
