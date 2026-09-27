import "./styles/tokens.css";
import "./styles/base.css";
import "./ui/ui.css";
import { render } from "preact";
import { App } from "./app";

render(<App />, document.getElementById("root")!);
