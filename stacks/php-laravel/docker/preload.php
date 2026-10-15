<?php

// opcache.preload: compile only the test 8 dataset into shared memory before the
// first request (docs/02 Appendix A). Preloading the framework itself is a
// potential optimization for the tuned round (docs/03), so it isn't done here.
opcache_compile_file('/var/www/html/storage/bench/sales_data.php');
