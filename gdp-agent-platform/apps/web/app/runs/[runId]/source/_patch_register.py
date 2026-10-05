from pathlib import Path

p = Path(__file__).with_name("register-form.tsx")
text = p.read_text(encoding="utf-8")
text = text.replace(
    '{intent.domain_name && <p className="text-muted-foreground">Domain {intent.domain_name}</p>}',
    '{intent.domain_name && !/GDP/i.test(intent.domain_name) && (\n              <p className="text-muted-foreground">Domain {intent.domain_name}</p>\n            )}',
)
old = '''            <div>
              <Label htmlFor="source_system_name">Source system name</Label>
              <Input id="source_system_name" name="source_system_name" required pattern="[A-Za-z][A-Za-z0-9_]{0,63}"
                     defaultValue={intent?.source.source_system_name || ""} placeholder="CRM" />
            </div>
            <div>'''
new = '''            <input type="hidden" name="source_system_name" value={intent?.source.source_system_name || schema || database} />
            <div>'''
if old not in text:
    raise SystemExit("source system block not found")
p.write_text(text.replace(old, new, 1), encoding="utf-8")
print("patched register-form")
