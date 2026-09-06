-- Fails if any province's total population is not strictly positive.
select *
from {{ ref('population_by_province_dbt') }}
where total_population <= 0
