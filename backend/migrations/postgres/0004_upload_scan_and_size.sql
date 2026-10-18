-- Upload scan state and per-image size (quota accounting). "clean" is only ever stored when a configured scanner reported it; without a scanner the value is "unscanned".
alter table app.documents add column scan_status text not null default 'unscanned' check (scan_status in ('clean', 'unscanned'));
alter table app.images add column scan_status text not null default 'unscanned' check (scan_status in ('clean', 'unscanned'));
alter table app.images add column size_bytes bigint default 0;
