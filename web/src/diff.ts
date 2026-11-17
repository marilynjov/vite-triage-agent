/** A plain unified-diff parser: files, hunk headers and line kinds, nothing more. */

export type DiffLine = { kind: 'add' | 'del' | 'ctx' | 'hunk'; text: string }
export type DiffFile = { path: string; lines: DiffLine[] }

const FILE = /^diff --git a\/.* b\/(.*)$/

export function parseDiff(text: string): DiffFile[] {
  const files: DiffFile[] = []
  let file: DiffFile | null = null
  let inHunk = false // before a file's first @@, every line is header (---, +++, index, mode)
  for (const line of text.split('\n')) {
    const m = FILE.exec(line)
    if (m) {
      file = { path: m[1], lines: [] }
      files.push(file)
      inHunk = false
    } else if (file === null) {
      continue
    } else if (line.startsWith('@@')) {
      inHunk = true
      file.lines.push({ kind: 'hunk', text: line })
    } else if (!inHunk || line === '' || line.startsWith('\\')) {
      continue
    } else if (line[0] === '+') {
      file.lines.push({ kind: 'add', text: line.slice(1) })
    } else if (line[0] === '-') {
      file.lines.push({ kind: 'del', text: line.slice(1) })
    } else if (line[0] === ' ') {
      file.lines.push({ kind: 'ctx', text: line.slice(1) })
    }
  }
  return files
}
