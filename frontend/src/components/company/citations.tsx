// Every place the note shows prose goes through here, so a citation number is a button wherever it
// appears. The debate tab used to render the same numbers as plain text, which is the one thing a citation
// must never be.
const INLINE = /\[(\d+)\]/g

export function CitedText({ text, onCite }: { text: string; onCite: (n: number) => void }) {
  const parts: (string | number)[] = []
  let last = 0
  for (const match of text.matchAll(INLINE)) {
    if (match.index > last) parts.push(text.slice(last, match.index))
    parts.push(Number(match[1]))
    last = match.index + match[0].length
  }
  if (last < text.length) parts.push(text.slice(last))

  return (
    <>
      {parts.map((part, index) => typeof part === 'number' ? (
        <button key={index} className="cite" onClick={() => onCite(part)}
                aria-label={`Show source ${part}`}>[{part}]</button>
      ) : <span key={index}>{part}</span>)}
    </>
  )
}
