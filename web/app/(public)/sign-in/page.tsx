import Link from "next/link";

import { IconOffice, IconPerson } from "@/components/ui/icons";
import { pageMetadata } from "@/lib/metadata";

export const metadata = pageMetadata({
  title: "Sign in",
  description: "Sign in to LandChain as a department officer or as a citizen.",
  path: "/sign-in",
  noindex: true,
});

export default function SignInChooser() {
  return (
    <div className="page-narrow">
      <header className="page-head">
        <h1>Sign in</h1>
        <p>Officers sign in with their department account. Citizens sign in with DigiLocker and the key in their browser.</p>
      </header>
      <div className="doors" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))" }}>
        <section className="door" style={{ ["--door" as string]: "var(--reg)" }} aria-labelledby="who-officer">
          <IconOffice className="door-art" />
          <h2 id="who-officer" style={{ fontSize: "var(--step-1)" }}>
            I work in a department
          </h2>
          <p>Registration, Revenue or the Collectorate. Your actions are signed with your own certificate.</p>
          <div className="cluster">
            <Link className="btn btn-primary" href="/officials/sign-in">
              Officer sign-in
            </Link>
          </div>
        </section>
        <section className="door" style={{ ["--door" as string]: "var(--ink)" }} aria-labelledby="who-citizen">
          <IconPerson className="door-art" />
          <h2 id="who-citizen" style={{ fontSize: "var(--step-1)" }}>
            I own or am buying land
          </h2>
          <p>Sign with the key you created when you registered. New here? Registering takes a minute.</p>
          <div className="cluster">
            <Link className="btn btn-primary" href="/citizen/sign-in">
              Citizen sign-in
            </Link>
            <Link className="btn" href="/citizen/register">
              Register
            </Link>
          </div>
        </section>
      </div>
    </div>
  );
}
