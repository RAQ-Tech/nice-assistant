def clean_endpoint($container):
  {
    Aliases: ((.Aliases // [])
      | map(select(. != $container.Id and . != ($container.Id[0:12])))
      | if length == 0 then null else . end),
    Links: (.Links // null),
    DriverOpts: (.DriverOpts // null),
    IPAMConfig: (.IPAMConfig // null),
    MacAddress: ((.MacAddress // "") | if . == "" then null else . end),
    GwPriority: ((.GwPriority // 0) | if . == 0 then null else . end)
  } | with_entries(select(.value != null and .value != {}));

.[0] as $container |
(($image_labels // {})
  | with_entries(select(.key | startswith("org.opencontainers.image.")))) as $image_labels |
($container.Config
  | if .Hostname == ($container.Id[0:12]) then del(.Hostname) else . end
  | .Labels = ((.Labels // {}) + $image_labels)
  | .Image = $image) +
{
  HostConfig: $container.HostConfig,
  NetworkingConfig: {
    EndpointsConfig: (($container.NetworkSettings.Networks // {})
      | with_entries(.value |= clean_endpoint($container)))
  }
}
