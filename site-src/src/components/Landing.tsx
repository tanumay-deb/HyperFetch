/* What someone sees before they have an account. Three lines that say what the
   thing does, in the order it happens. */
export default function Landing({ onStart }: { onStart: () => void }) {
  return (
    <div className="landing">
      <header>
        <span className="mark" aria-hidden="true">⚡</span>
        <span className="wordmark">HyperFetch</span>
      </header>

      <div className="hero">
        <div className="hero-inner">
          <h1>Torrents your phone can&rsquo;t download, downloaded anyway.</h1>
          <p>
            Paste a magnet link here and a machine at home fetches it. When it is
            done, save the file straight to your phone.
          </p>

          <ol className="steps">
            <li className="step">
              <b>01</b><span>Paste a magnet or a link.</span>
            </li>
            <li className="step">
              <b>02</b><span>The machine at home does the downloading.</span>
            </li>
            <li className="step">
              <b>03</b><span>Save the finished file to your device.</span>
            </li>
          </ol>

          <button className="primary" onClick={onStart}>Sign in</button>
        </div>
      </div>

      <footer>You need an invite code to create an account.</footer>
    </div>
  );
}
