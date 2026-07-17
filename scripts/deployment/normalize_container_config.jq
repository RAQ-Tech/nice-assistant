.[0] as $container |
{
  Config: ($container.Config
    | del(.Image)
    | if .Hostname == ($container.Id[0:12]) then .Hostname = "__docker_default__" else . end
    | .Labels = ((.Labels // {})
      | del(.["com.nice-assistant.guard-update"])
      | with_entries(select(.key | startswith("org.opencontainers.image.") | not)))),
  HostConfig: ($container.HostConfig
    | .OomKillDisable = (.OomKillDisable // false)),
  Networks: (($container.NetworkSettings.Networks // {}) | with_entries(.value |= {
    Aliases: ((.Aliases // [])
      | map(select(. != $container.Id
        and . != ($container.Id[0:12])
        and . != ($container.Name | ltrimstr("/"))
        and . != $managed_name))
      | sort
      | if length == 0 then null else . end),
    Links: (.Links // null),
    DriverOpts: (.DriverOpts // null),
    IPAMConfig: (.IPAMConfig // null),
    MacAddress: ((.MacAddress // "") | if . == "" then null else . end),
    GwPriority: (.GwPriority // 0)
  }))
}
