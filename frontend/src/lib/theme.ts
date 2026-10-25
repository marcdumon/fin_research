// Dark is the default because this screen is read for long stretches beside a terminal. Light exists
// because a research note is prose, and prose on a dark page is harder to read for anyone who finds it so.

export type Theme = 'dark' | 'light'

const KEY = 'fin_research.theme'

export function storedTheme(): Theme {
  try {
    return localStorage.getItem(KEY) === 'light' ? 'light' : 'dark'
  } catch {
    return 'dark'
  }
}

export function applyTheme(theme: Theme): void {
  document.documentElement.setAttribute('data-theme', theme)
  try {
    localStorage.setItem(KEY, theme)
  } catch {
    // A browser refusing storage is not a reason to refuse the theme.
  }
}
