/**
 * P8 acceptance in a real VS Code Extension Host (run under `xvfb-run`
 * on Linux). Scaffolds a scratch repository with the muvue CLI, starts
 * `muvue serve` on it, and puts a logging proxy in front of the daemon
 * so the suite can see what the webview's iframe requested. Then it
 * launches VS Code with this extension and `./suite`.
 *
 * `MUVUE_CMD` is the command that runs the CLI (default `muvue`). The
 * api token is read from `serve`'s stdout and handed to the Extension
 * Host through its environment; it is never written to disk.
 */

import { ChildProcess, spawn, spawnSync } from "child_process";
import * as fs from "fs";
import * as http from "http";
import * as net from "net";
import * as os from "os";
import * as path from "path";
import { runTests } from "@vscode/test-electron";

const MUVUE = (process.env.MUVUE_CMD ?? "muvue").split(" ").filter(Boolean);

function run(cmd: string[], cwd: string): string {
  const r = spawnSync(cmd[0], cmd.slice(1), { cwd, encoding: "utf8" });
  if (r.status !== 0) throw new Error(`${cmd.join(" ")} exited ${r.status}: ${r.stderr}`);
  return r.stdout;
}

function muvue(repo: string, ...args: string[]): { id?: number } {
  const out = run([...MUVUE, ...args, "--path", repo], repo);
  return out.trim().startsWith("{") ? JSON.parse(out) : {};
}

/** A project whose task sits in `review`, ready for `approveNode`. */
function scaffold(repo: string): { projectId: number; nodeId: number } {
  run(["git", "init", "-q", "-b", "main"], repo);
  run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "initial"], repo);
  run([...MUVUE, "init", repo], repo);
  const projectId = muvue(repo, "project", "create", "--goal", "extension host test").id!;
  const specId = muvue(repo, "spec", String(projectId), "--title", "s", "--body", "b").id!;
  muvue(repo, "approve", `spec:${specId}`);
  const nodeId = muvue(repo, "decompose", String(specId), "--title", "t", "--criteria", "c").id!;
  muvue(repo, "approve", `gate2:${projectId}`);
  muvue(repo, "start", String(nodeId), "--owner", "host-test");
  muvue(repo, "done", String(nodeId), "--owner", "host-test");
  return { projectId, nodeId };
}

async function freePort(): Promise<number> {
  return new Promise((resolve) => {
    const server = net.createServer().listen(0, "127.0.0.1", () => {
      const port = (server.address() as net.AddressInfo).port;
      server.close(() => resolve(port));
    });
  });
}

async function startDaemon(repo: string, port: number): Promise<{ proc: ChildProcess; token: string }> {
  const proc = spawn(MUVUE[0], [...MUVUE.slice(1), "serve", repo, "--port", String(port)], {
    stdio: ["ignore", "pipe", "inherit"],
  });
  const token = await new Promise<string>((resolve, reject) => {
    let buffered = "";
    const timer = setTimeout(() => reject(new Error("muvue serve printed no api token")), 30000);
    proc.stdout!.on("data", (chunk: Buffer) => {
      buffered += chunk.toString();
      const match = /^api token: (\S+)$/m.exec(buffered);
      if (match) {
        clearTimeout(timer);
        resolve(match[1]);
      }
    });
    proc.on("exit", (code) => reject(new Error(`muvue serve exited ${code}`)));
  });
  return { proc, token };
}

/** Forwards to the daemon, rewriting Host and Origin to the daemon's own
 * (it refuses any other), and records each request line. `GET /__log`
 * returns the record instead of being forwarded. */
function startProxy(daemonPort: number): Promise<{ server: http.Server; url: string }> {
  const seen: string[] = [];
  const daemonOrigin = `http://127.0.0.1:${daemonPort}`;
  const server = http.createServer((req, res) => {
    if (req.url === "/__log") {
      res.setHeader("Content-Type", "application/json");
      res.end(JSON.stringify(seen));
      return;
    }
    seen.push(`${req.method} ${(req.url ?? "").split("?")[0]}`);
    const headers = { ...req.headers, host: `127.0.0.1:${daemonPort}` };
    if (headers.origin) headers.origin = daemonOrigin;
    delete headers.referer;
    const upstream = http.request(
      { host: "127.0.0.1", port: daemonPort, method: req.method, path: req.url, headers },
      (up) => {
        res.writeHead(up.statusCode ?? 502, up.headers);
        up.pipe(res);
      },
    );
    upstream.on("error", () => res.destroy());
    req.pipe(upstream);
  });
  return new Promise((resolve) =>
    server.listen(0, "127.0.0.1", () =>
      resolve({ server, url: `http://127.0.0.1:${(server.address() as net.AddressInfo).port}` }),
    ),
  );
}

async function main(): Promise<void> {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), "muvue-host-"));
  const { projectId, nodeId } = scaffold(repo);
  const { proc, token } = await startDaemon(repo, await freePort());
  const daemonPort = Number(/--port (\d+)/.exec(proc.spawnargs.join(" "))![1]);
  const proxy = await startProxy(daemonPort);
  try {
    const extensionDevelopmentPath = path.resolve(__dirname, "../../..");
    await runTests({
      extensionDevelopmentPath,
      extensionTestsPath: path.resolve(__dirname, "./suite"),
      launchArgs: [repo, "--disable-extensions", "--disable-workspace-trust", "--disable-gpu"],
      extensionTestsEnv: {
        MUVUE_TEST_URL: proxy.url,
        MUVUE_TEST_TOKEN: token,
        MUVUE_TEST_PROJECT: String(projectId),
        MUVUE_TEST_NODE: String(nodeId),
      },
    });
  } finally {
    proxy.server.close();
    proc.kill("SIGTERM");
    fs.rmSync(repo, { recursive: true, force: true });
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
