select
    province,
    age_bracket,
    population,
    period
from {{ source('ducklake_main', 'raw_population') }}
