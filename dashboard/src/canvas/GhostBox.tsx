import { Button } from "../ui/Button";

export function GhostBox({ title, caption, error, onRetry, logLink, style }: { title: string; caption: string; error?: string; onRetry?: () => void; logLink?: string; style?: Record<string, string | number> }) {
  return (
    <div class={"task-box ghost" + (error ? " ghost-error" : "")} style={style}>
      <div class="title">{title}</div>
      {error ? (
        <div class="stack tight">
          <div class="caption danger">{error}</div>
          {onRetry ? <Button variant="plain" onClick={onRetry}>Retry</Button> : null}
        </div>
      ) : (
        <div class="row">
          <span class="pulse" aria-hidden="true" />
          <span class="caption">{caption}</span>
          {logLink ? <a href={logLink} class="caption">view log</a> : null}
        </div>
      )}
    </div>
  );
}
