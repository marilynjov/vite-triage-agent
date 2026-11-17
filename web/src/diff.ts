/**
 * A plain unified-diff parser: files, hunk headers and line kinds, nothing more.
 *
 * It reads both git output (`diff --git` then `---`/`+++`) and the agent's
 * stored patches, which are difflib output with no `diff --git` line. Hunk
 * line counts decide what a `---` or `+++` line is: inside a hunk it is a
 * removed or added line (whose text starts `--` or `++`); only once the hunk's
 * counts are used up is it a file header.
 */

export type DiffLine = { kind: 'add' | 'del' | 'ctx' | 'hunk'; text: string }
export type DiffFile = { path: string; lines: DiffLine[] }

const GIT = /^diff --git a\/.* b\/(.*)$/
const HUNK = /^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@/

function headerPath(line: string): string {
  return line.slice(4).replace(/^"|"$/g, '').replace(/^b\//, '')
}

export function parseDiff(text: string): DiffFile[] {
  const files: DiffFile[] = []
  let file: DiffFile | null = null
  let oldLeft = 0
  let newLeft = 0
  for (const line of text.split('\n')) {
    if (oldLeft > 0 || newLeft > 0) {
      if (line.startsWith('\\')) continue // "\ No newline at end of file"
      const marker = line[0] ?? ' ' // a blank line is context whose space was stripped
      const body = line.slice(1)
      if (marker === '+') {
        file?.lines.push({ kind: 'add', text: body })
        newLeft--
      } else if (marker === '-') {
        file?.lines.push({ kind: 'del', text: body })
        oldLeft--
      } else {
        file?.lines.push({ kind: 'ctx', text: body })
        oldLeft--
        newLeft--
      }
      continue
    }
    const git = GIT.exec(line)
    const hunk = HUNK.exec(line)
    if (git) {
      file = { path: git[1], lines: [] }
      files.push(file)
    } else if (line.startsWith('+++ ')) {
      // git already opened this file (no hunks yet); difflib has no other header
      if (file === null || file.lines.length > 0) {
        file = { path: headerPath(line), lines: [] }
        files.push(file)
      }
    } else if (hunk && file !== null) {
      oldLeft = hunk[1] === undefined ? 1 : Number(hunk[1])
      newLeft = hunk[2] === undefined ? 1 : Number(hunk[2])
      file.lines.push({ kind: 'hunk', text: line })
    }
  }
  return files
}
