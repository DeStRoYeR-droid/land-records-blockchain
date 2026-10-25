import Link from "next/link";

export function SiteFooter() {
  return (
    <footer className="site-footer">
      <div className="page">
        <nav aria-labelledby="f-public">
          <h2 id="f-public">Public</h2>
          <ul>
            <li>
              <Link href="/register">Land register</Link>
            </li>
            <li>
              <Link href="/verify">Verify a validator report</Link>
            </li>
            <li>
              <Link href="/how-it-works">How LandChain works</Link>
            </li>
          </ul>
        </nav>
        <nav aria-labelledby="f-desks">
          <h2 id="f-desks">Dashboards</h2>
          <ul>
            <li>
              <Link href="/officials">Department officers</Link>
            </li>
            <li>
              <Link href="/citizen">Citizens</Link>
            </li>
            <li>
              <Link href="/agent">The AI validator</Link>
            </li>
          </ul>
        </nav>
        <div>
          <h2>Under the hood</h2>
          <p>
            Python nodes in the style of Hyperledger Fabric: certificate authorities, three Raft orderers and one
            endorsing peer per department. The validator explains its findings with a local MLX model through oMLX.
          </p>
        </div>
        <p className="disclaimer">
          LandChain is a demonstration system built as a student project. It is not a Government of Telangana
          service, and its records, people and documents are test data.
        </p>
      </div>
    </footer>
  );
}
