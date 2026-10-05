"use client";

import { useEffect, useState, useTransition } from "react";
import { loadSnowflakeRoles, setSnowflakeRole } from "@/app/role-actions";

export function RoleSelector({ currentRole }: { currentRole: string | null }) {
  const [roles, setRoles] = useState<string[]>(currentRole ? [currentRole] : []);
  const [value, setValue] = useState(currentRole ?? "");
  const [error, setError] = useState("");
  const [pending, start] = useTransition();

  useEffect(() => {
    loadSnowflakeRoles().then((result) => {
      if (!result.ok) {
        setError(result.error);
        return;
      }
      setRoles(result.data.roles);
      setValue(result.data.current);
    });
  }, []);

  return (
    <div className="mt-3 border-t border-white/10 pt-3">
      <label htmlFor="snowflake_role" className="mb-1 block text-[10px] font-semibold uppercase tracking-wide text-white/45">
        Snowflake role
      </label>
      <select
        id="snowflake_role"
        className="w-full rounded-md border border-white/15 bg-white/10 px-2 py-1.5 text-xs text-white"
        value={value}
        disabled={pending || roles.length === 0}
        onChange={(e) => {
          const next = e.target.value;
          setValue(next);
          setError("");
          start(async () => {
            const result = await setSnowflakeRole(next);
            if (!result.ok) setError(result.error);
          });
        }}
      >
        {roles.map((role) => (
          <option key={role} value={role} className="text-black">
            {role}
          </option>
        ))}
      </select>
      <p className="mt-1 text-[10px] leading-snug text-white/45">
        Catalog browse and source register use this role. Pick one that can see your bronze/silver objects.
      </p>
      {error && <p className="mt-1 text-[10px] text-red-300">{error}</p>}
    </div>
  );
}
