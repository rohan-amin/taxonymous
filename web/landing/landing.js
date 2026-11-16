"use strict";
const VIEWS = {
  orig: { ssn: "987-65-4321", name: "Paul T. Williams", addr: "1842 Larkspur Lane, Columbus, OH 43214",
    note: "<strong>The original.</strong> Your name, SSN and address, next to the wages that go on your return." },
  black: { ssn: '<span class="bar">987-65-4321</span>', name: '<span class="bar">Paul T. Williams</span>', addr: '<span class="bar">1842 Larkspur Lane, Columbus</span> OH <span class="bar">43214</span>',
    note: "<strong>Blacked out.</strong> Private, but whose W-2 is this? With two in the pile, AI can't tell." },
  tax: { ssn: '<span class="label">&lt;TAXPAYER_SSN&gt;</span>', name: '<span class="label">&lt;TAXPAYER&gt;</span>', addr: '<span class="label">&lt;HOME_ADDR&gt;</span> OH <span class="label">&lt;HOME_ADDR&gt;</span>',
    note: "<strong>Taxonymous.</strong> Private, and AI still knows this is your W-2, from your home address. The amounts are untouched." },
};
function show(v) {
  document.querySelectorAll(".seg button").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.v === v)));
  document.querySelectorAll(".w2 [data-f]").forEach(el => { el.innerHTML = VIEWS[v][el.dataset.f]; });
  document.getElementById("demo-note").innerHTML = VIEWS[v].note;
}
document.querySelectorAll(".seg button").forEach(b => b.addEventListener("click", () => show(b.dataset.v)));
show("tax");

const LABELS = "Personal details in these documents were replaced with labels before I uploaded them. <TAXPAYER> is me, <SPOUSE> is my spouse, <DEPENDENT_1> and so on are our dependents. <TRUST_1> is a family trust that sent us a K-1. Account labels say whose account it is and what kind, such as <TAXPAYER_ROTH_IRA_1> or <JOINT_BROKERAGE_1>. Dollar amounts, box numbers and payer names are unchanged. Refer to people and accounts by their labels, and don't ask for the real details: you don't need them.";
const PROMPTS = {
  "t-check": `I've attached my tax documents for 2025 and a draft of my return.\n\n${LABELS}\n\nPlease check the return against the documents:\n1. List each document, what kind it is, and whose it is.\n2. Check that every amount made it to the right line of the return, for the right person.\n3. Flag anything missing, counted twice, or on the wrong line, with the document and box it came from.\n4. Check limits and credits that depend on who earned what: the Social Security wage cap, retirement contribution limits, credits for dependents.\n5. Finish with a short list of what to fix, most important first.`,
  "t-trust": `I've attached the 2025 tax documents for a trust and a draft of its Form 1041.\n\nIdentifying details were replaced with labels before I uploaded them. <TRUST> is the trust and <TRUST_EIN> its EIN. <TRUSTEE_1> and so on are its trustees, <GRANTOR> the person who created it, and <BENEFICIARY_1> and so on its beneficiaries. Dollar amounts, box numbers and payer names are unchanged. Refer to everyone by their labels, and don't ask for the real details: you don't need them.\n\nPlease check the return against the documents:\n1. List each document, what kind it is, and what it reports.\n2. Check that every amount made it to the right line of the 1041.\n3. Check distributable net income, the distribution deduction, and how income is split between the trust and each beneficiary on their K-1s.\n4. Flag anything missing, counted twice, or on the wrong line, with the document and box it came from.\n5. Finish with a short list of what to fix, most important first.`,
  "t-missing": `I've attached my tax documents for 2025.\n\n${LABELS}\n\nBased on these documents:\n1. What other forms would you expect me to have that aren't here? For example, a 1099 for an account that shows interest, or a 5498 for an IRA contribution.\n2. Is anything here from the wrong year, or a duplicate?\n3. What questions should I be able to answer before filing?`,
  "t-explain": `I've attached my tax documents for 2025.\n\n${LABELS}\n\nFor each document, explain in plain language what it is, which numbers matter for my return, and where they usually go on Form 1040 or its schedules. Point out anything unusual.`,
};
function tab(id) {
  document.querySelectorAll(".prompts .tabs button").forEach(b => b.setAttribute("aria-selected", String(b.id === id)));
  document.getElementById("prompt-text").textContent = PROMPTS[id];
  document.getElementById("p-body").setAttribute("aria-labelledby", id);
  document.getElementById("copied").textContent = "";
}
document.querySelectorAll(".prompts .tabs button").forEach(b => b.addEventListener("click", () => tab(b.id)));
tab("t-check");
document.getElementById("copy").addEventListener("click", () => {
  const pre = document.getElementById("prompt-text"), out = document.getElementById("copied");
  const select = () => { const r = document.createRange(); r.selectNodeContents(pre); const s = getSelection(); s.removeAllRanges(); s.addRange(r); out.textContent = "Selected: press Ctrl+C or ⌘C to copy."; };
  try { navigator.clipboard.writeText(pre.textContent).then(() => { out.textContent = "Copied. Paste it into your AI assistant with your safe copies."; }, select); } catch { select(); }
});

// set up in this browser already (the app keeps its files here): open it, don't start over
(async () => {
  try {
    if (!(await caches.has("taxonymous-app-current"))) return;
    document.querySelectorAll("[data-open]").forEach((a) => { a.textContent = "Open Taxonymous"; });
  } catch { /* no storage here: Get started is right */ }
})();
