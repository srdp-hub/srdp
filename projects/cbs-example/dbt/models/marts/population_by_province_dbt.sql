select
    province,
    sum(population) as total_population
from {{ ref('stg_population') }}
group by province
