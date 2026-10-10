/** A stored database or schema name (INFORMATION_SCHEMA spelling) -> the form the API resolves back to that exact
 *  name. The API upper-cases unquoted names, so anything other than a simple upper-case name is sent double-quoted. */
export function sfIdent(name: string): string {
  return /^[A-Z_][A-Z0-9_$]*$/.test(name) ? name : `"${name.replace(/"/g, '""')}"`;
}
