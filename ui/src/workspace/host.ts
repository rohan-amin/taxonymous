import { useSyncExternalStore } from "react";

// The browser app (web/static/boot.js) is the "host": it sets Taxonymous up on this computer,
// opens and saves your Taxonymous file, runs the engines, and writes safe copies where you
// choose. The UI shows its state and asks it to act. Absent in the Mac app and `taxonymous serve`.

export type Stage =
  | "checking"     // looking at what's on this computer
  | "setup"        // a one-time download is needed
  | "downloading"  // it's happening
  | "ready"        // set up: open or start your file
  | "starting"     // your file is being opened, the engines started
  | "open";        // the workspace

export interface HostFile {
  name: string;
  protected: boolean;  // with a passphrase
  dirty: boolean;      // changed since it was opened or saved
  inPlace: boolean;    // Save writes over it (Chrome, Edge); otherwise a new copy is downloaded
  auto: boolean;       // saved by itself when it changes (Chrome, Edge, once it has a place you let it write to)
  savedAt: number | null;  // when it was last saved (ms), as far as this page knows; null: never
  saving: boolean;
  error: string;       // the last save by itself didn't work
}

export interface HostState {
  stage: Stage;
  size: string;                                       // what setup (or an update) downloads
  update: boolean;                                    // set up, and a new version is waiting
  progress: { fraction: number; text: string } | null;
  error: string;
  file: HostFile | null;
}

export type PassMode = "create" | "open";
export type PassAnswer = { passphrase: string } | null;  // null: cancelled

export interface Host {
  readonly state: HostState;
  readonly folder: boolean;  // safe copies go to a folder you choose (Chrome, Edge); otherwise downloaded
  subscribe(listener: () => void): () => void;
  setUp(): void;
  remove(): void;
  openFile(): void;
  newFile(from: "empty" | "sample" | { profile: string }): void;
  // guided setup's last step: where the file goes (a Save dialog, in Chrome and Edge), then, once
  // setup's download is done, the details checked as the app keeps them, opened and saved there.
  // { errors } by field ("people.0.ssn") if something needs fixing; null if you cancelled.
  create(profile: object, passphrase: string): Promise<{ name: string; downloaded: boolean }
    | { errors: Record<string, string> } | null>;
  save(as?: boolean): Promise<{ name: string; downloaded: boolean } | null>;
  allowSaving(): Promise<boolean>;  // ask (on a click) to save to the opened file
  close(): void;
  saveCopies(ids: string[]): Promise<string | null>; // where they went; null: you cancelled
  // set by the UI: ask for a passphrase (to protect a new file, or to open one)
  askPass?: (mode: PassMode, name: string, error: string) => Promise<PassAnswer>;
}

export const HOST = (window as unknown as { taxonymousHost?: Host }).taxonymousHost;

const never = () => () => undefined;
const nothing = () => null;
export function useHost(): HostState | null {
  return useSyncExternalStore(HOST ? HOST.subscribe : never, HOST ? () => HOST.state : nothing);
}
