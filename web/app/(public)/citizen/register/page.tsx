import { CitizenRegister } from "@/components/auth/citizen-register";
import { pageMetadata } from "@/lib/metadata";

export const metadata = pageMetadata({
  title: "Register as a citizen",
  description: "Create a DigiLocker account and a signing key in your browser to own and transfer land on LandChain.",
  path: "/citizen/register",
});

export default function Page() {
  return (
    <div className="page-narrow">
      <header className="page-head">
        <h1>Register as a citizen</h1>
        <p>One minute: a DigiLocker account for your documents and a signing key for your sales.</p>
      </header>
      <CitizenRegister />
    </div>
  );
}
