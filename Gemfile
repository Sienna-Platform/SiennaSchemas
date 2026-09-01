source "https://rubygems.org"

# Ruby 3.4 removed these from the default gem set; Jekyll 4.3.4 still requires them.
gem "csv"
gem "base64"
gem "logger"
gem "ostruct"
gem "bigdecimal"

gem "jekyll", "4.3.4"
# Fetches remote_theme in _config.yml. just-the-docs itself is not a direct
# dependency here on purpose -- see _config.yml's comment.
gem "jekyll-remote-theme", "0.4.3"
# Rewrites relative links to .md files (e.g. "ThermalStandard.md" in the
# generated type-index tables) to the built pretty-permalink URL.
gem "jekyll-relative-links", "0.7.0"
# just-the-docs' own runtime dependencies; the GitHub Pages gem set carries
# them already, but our own Gemfile-driven build needs them declared explicitly.
gem "jekyll-seo-tag", "2.8.0"
gem "jekyll-include-cache", "0.2.1"
