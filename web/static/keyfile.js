// Your Taxonymous file: your profile and the vault (each label and the real value it stands
// for), kept only where you save it, never in the browser. Protected by a passphrase by
// default: AES-256-GCM, the key derived from the passphrase with PBKDF2 (SHA-256, 600,000
// rounds), all with the browser's own cryptography (WebCrypto). A wrong passphrase, or a
// changed file, fails to open: GCM checks every byte.
"use strict";

const KEYFILE = (() => {
  const ROUNDS = 600000;
  const enc = new TextEncoder();
  const dec = new TextDecoder();
  const b64 = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf)));
  const unb64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));

  async function key(passphrase, salt, rounds) {
    const base = await crypto.subtle.importKey("raw", enc.encode(passphrase), "PBKDF2", false, ["deriveKey"]);
    return crypto.subtle.deriveKey({ name: "PBKDF2", hash: "SHA-256", salt, iterations: rounds }, base,
                                   { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
  }

  // contents: { profile, vault } -> the file's text
  async function seal(contents, passphrase) {
    const body = JSON.stringify(contents);
    if (!passphrase) return JSON.stringify({ taxonymous: 1, protected: false, contents: JSON.parse(body) }, null, 1);
    const salt = crypto.getRandomValues(new Uint8Array(16));
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const data = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, await key(passphrase, salt, ROUNDS), enc.encode(body));
    return JSON.stringify({ taxonymous: 1, protected: true,
                            kdf: { name: "PBKDF2", hash: "SHA-256", rounds: ROUNDS, salt: b64(salt) },
                            cipher: { name: "AES-GCM", iv: b64(iv) }, data: b64(data) });
  }

  function isProtected(text) {
    return JSON.parse(text).protected === true;
  }

  // the file's text -> { profile, vault }; throws "wrong passphrase" for a bad one
  async function open(text, passphrase) {
    const f = JSON.parse(text);
    if (f.taxonymous !== 1) throw new Error("This isn't a Taxonymous file.");
    if (!f.protected) return f.contents;
    try {
      const k = await key(passphrase || "", unb64(f.kdf.salt), f.kdf.rounds);
      const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv: unb64(f.cipher.iv) }, k, unb64(f.data));
      return JSON.parse(dec.decode(plain));
    } catch {
      throw new Error("That passphrase doesn't open this file.");
    }
  }

  return { seal, open, isProtected };
})();
