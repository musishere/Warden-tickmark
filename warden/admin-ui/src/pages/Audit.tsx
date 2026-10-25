/* Audit log: every decision Warden made for the firm, newest first, 100 at a time. Append-only in the database. */
import { useEffect, useMemo, useState } from "react";
import { Lock } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Empty, LoadError, Loading, PageHeader, SelectField } from "@/components/common";
import { api, useData, type Agent, type AuditRow, type Engagement, type Person } from "@/lib/api";

const PAGE = 100;
const ALL = "all";

export default function Audit() {
  const engagements = useData<Engagement[]>("/admin/engagements");
  const people = useData<Person[]>("/admin/people");
  const agents = useData<Agent[]>("/admin/agents");
  const [filter, setFilter] = useState(ALL);
  const [rows, setRows] = useState<AuditRow[] | null>(null);
  const [more, setMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const page = (before?: number) =>
    api<AuditRow[]>("GET", `/admin/audit?limit=${PAGE}${filter !== ALL ? `&engagement=${filter}` : ""}${before ? `&before=${before}` : ""}`);

  useEffect(() => {
    setRows(null);
    page().then((r) => { setRows(r); setMore(r.length === PAGE); }, (e: Error) => setError(e.message));
  }, [filter]);

  const names = useMemo(() => Object.fromEntries(
    [...(engagements.data ?? []), ...(people.data ?? []), ...(agents.data ?? [])].map((x) => [x.id, x.name])), [engagements.data, people.data, agents.data]);
  const name = (id: string | null, none: string) => (id ? names[id] ?? id.slice(0, 8) : none);

  return (
    <>
      <PageHeader
        title="Audit log"
        description="Every decision, allowed or refused, with the reason; every admin change with who made it. The database itself refuses to change or delete a row."
        actions={
          <SelectField className="w-64" defaultValue={ALL} onChange={setFilter}
            items={[{ value: ALL, label: "All engagements + firm-wide" }, ...(engagements.data ?? []).map((e) => ({ value: e.id, label: e.name }))]} />
        }
      />
      <Card>
        <CardContent>
          {error ? <LoadError message={error} /> : !rows ? <Loading rows={8} /> : rows.length === 0 ? <Empty title="Nothing recorded yet" /> : (
            <>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>When</TableHead><TableHead>Who</TableHead><TableHead>Agent</TableHead><TableHead>Engagement</TableHead>
                    <TableHead>Action</TableHead><TableHead>Decision</TableHead><TableHead>Reason</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((r) => (
                    <TableRow key={r.id}>
                      <TableCell className="whitespace-nowrap text-xs text-muted-foreground">{new Date(r.at).toLocaleString()}</TableCell>
                      <TableCell>{name(r.userId, "—")}</TableCell>
                      <TableCell className="text-muted-foreground">{name(r.agentId, "—")}</TableCell>
                      <TableCell className="text-muted-foreground">{name(r.engagementId, "Firm-wide")}</TableCell>
                      <TableCell>
                        <span className="font-mono text-xs">{r.action}</span>
                        {r.scope && <div className="font-mono text-xs text-muted-foreground">{r.scope}</div>}
                      </TableCell>
                      <TableCell>{r.allowed ? <Badge variant="secondary" className="bg-emerald-500/10 text-emerald-700 dark:text-emerald-400">Allowed</Badge> : <Badge variant="destructive">Refused</Badge>}</TableCell>
                      <TableCell className="max-w-md text-sm whitespace-normal">{r.reason}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <div className="flex items-center justify-between pt-4 text-xs text-muted-foreground">
                <span className="flex items-center gap-1.5"><Lock className="size-3.5" />Append-only: rows can't be edited or deleted</span>
                {more && (
                  <Button variant="outline" size="sm" onClick={() => page(rows[rows.length - 1].id).then((r) => { setRows([...rows, ...r]); setMore(r.length === PAGE); })}>
                    Load older
                  </Button>
                )}
              </div>
            </>
          )}
        </CardContent>
      </Card>
    </>
  );
}
