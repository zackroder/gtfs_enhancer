-- bus.lua — OSRM profile for bus routing (supports busways, transit corridors, psv access, and bus lanes)
local car = require("car")

function setup()
  local profile = car.setup()

  -- Allow 'bus' and 'psv' (Public Service Vehicle) in access whitelist
  profile.access_tag_whitelist['bus'] = true
  profile.access_tag_whitelist['psv'] = true

  -- Remove 'psv' from access blacklist so psv lanes/ways are accessible
  profile.access_tag_blacklist['psv'] = nil

  -- Prioritize bus and psv in access hierarchy over general motorcar/vehicle
  profile.access_tags_hierarchy = Sequence {
    'bus',
    'psv',
    'motorcar',
    'motor_vehicle',
    'vehicle',
    'access'
  }

  -- Include busways and bus guideways in routable highways
  profile.speeds.highway['busway'] = 40
  profile.speeds.highway['bus_guideway'] = 40
  profile.restricted_highway_whitelist['busway'] = true
  profile.restricted_highway_whitelist['bus_guideway'] = true

  -- Allow service access for bus routes (terminal roads, transit centers)
  profile.service_access_tag_blacklist['private'] = nil

  -- Include bus and psv restrictions
  profile.restrictions = Sequence {
    'bus',
    'psv',
    'motorcar',
    'motor_vehicle',
    'vehicle'
  }

  -- Adjust vehicle dimensions for transit buses
  profile.vehicle_height = 3.2
  profile.vehicle_length = 12.0

  return profile
end

return {
  setup = setup,
  process_way = car.process_way,
  process_node = car.process_node,
  process_turn = car.process_turn
}
