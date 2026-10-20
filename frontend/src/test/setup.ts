import "@testing-library/jest-dom/vitest";

// jsdom doesn't implement Element.scrollTo - components that call it
// (e.g. ChatView auto-scrolling to the latest message) would otherwise
// throw in every test that renders them.
if (typeof Element !== "undefined" && !Element.prototype.scrollTo) {
  Element.prototype.scrollTo = () => {};
}
