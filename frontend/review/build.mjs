import { build } from "esbuild";
import { copyFile, readFile, readdir, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
const out = "../../src/resume_studio/review_component";
const result = await build({
  entryPoints: ["src.js"],
  bundle: true,
  minify: true,
  outfile: `${out}/app.js`,
  target: ["es2020"],
  legalComments: "eof",
  metafile: true,
});
await Promise.all(
  ["index.html", "style.css"].map((f) => copyFile(f, `${out}/${f}`)),
);
// Include the full upstream licenses for every package shipped in the local bundle.
const packages = new Map();
for (const file of Object.keys(result.metafile.inputs)) {
  if (!file.startsWith("node_modules/")) continue;
  let dir = dirname(file);
  while (dir !== ".") {
    try {
      const pkg = JSON.parse(await readFile(join(dir, "package.json"), "utf8"));
      if (pkg.name) {
        packages.set(dir, pkg);
        break;
      }
    } catch {
      /* This directory is not a package root. */
    }
    dir = dirname(dir);
  }
}
const notices = [];
for (const [dir, pkg] of [...packages].sort()) {
  const names = (await readdir(dir)).filter((n) =>
    /^(licen[cs]e|copying|notice)(\.|$)/i.test(n),
  );
  if (!names.length) {
    const license = await readFile(`licenses/${pkg.name}.txt`, "utf8");
    notices.push(`${pkg.name} ${pkg.version} (${pkg.license})\n${license}`);
    continue;
  }
  notices.push(
    `${pkg.name} ${pkg.version} (${pkg.license})\n` +
      (
        await Promise.all(names.map((n) => readFile(join(dir, n), "utf8")))
      ).join("\n"),
  );
}
await writeFile(
  `${out}/THIRD_PARTY_LICENSES.txt`,
  notices
    .join("\n\n========================================\n\n")
    .replace(/[ \t\r]+$/gm, "")
    .trimEnd() + "\n",
);
