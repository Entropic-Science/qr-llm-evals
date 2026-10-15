import type { ReactNode } from "react";
export interface TableColumn<Row> { key: string; label: string; className?: string; render: (row: Row) => ReactNode }
interface TableProps<Row> { caption: string; columns: readonly TableColumn<Row>[]; rows: readonly Row[]; rowKey: (row: Row) => string }
export function Table<Row>({ caption, columns, rows, rowKey }: TableProps<Row>) { return <div className="table-frame"><table><caption className="sr-only">{caption}</caption><thead><tr>{columns.map((column) => <th key={column.key} className={column.className} scope="col">{column.label}</th>)}</tr></thead><tbody>{rows.map((row) => <tr key={rowKey(row)}>{columns.map((column) => <td key={column.key} className={column.className}>{column.render(row)}</td>)}</tr>)}</tbody></table></div>; }
