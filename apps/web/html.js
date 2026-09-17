// htm bound to preact's h: tagged templates instead of a JSX build step.
// Vendored (see vendor/SOURCES.json) — no bundler, no npm, no transpile.
import { h } from './vendor/preact.module.js';
import htm from './vendor/htm.module.js';

// One rule this template engine needs, and the only one it needs: htm's parser
// keeps a tag open until it is closed, so a VOID element written HTML-style
// swallows every later sibling as its child. Preact then inserts into an
// <input>/<img> and throws "insertBefore: parameter 1 is not of type Node" —
// the page stops rendering after the first update. Always self-close them:
//   <input ... />  <img ... />  <br />  <hr />  <meta ... />  <link ... />
// Every other tag needs its explicit close, exactly as in HTML. tests/test_studio.py
// renders the landing and the workshop in headless chromium and fails on either.
export default htm.bind(h);
