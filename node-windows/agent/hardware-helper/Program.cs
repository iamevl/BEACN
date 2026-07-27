using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using LibreHardwareMonitor.Hardware;

namespace NetworkDashboard.HardwareHelper;

internal sealed record SensorDto(
    string Name,
    string Type,
    float? Value,
    float? Minimum,
    float? Maximum,
    string Identifier
);

internal sealed record HardwareDto(
    string Name,
    string Type,
    string Identifier,
    IReadOnlyList<SensorDto> Sensors,
    IReadOnlyList<HardwareDto> SubHardware
);

internal sealed record DiagnosticDto(
    string Stage,
    string? HardwareType,
    string? HardwareName,
    string Status,
    string? Error
);

internal static class Program
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        WriteIndented = false,
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };

    private static readonly List<DiagnosticDto> Diagnostics = new();

    public static int Main(string[] args)
    {
        Console.OutputEncoding = Encoding.UTF8;

        Computer? computer = null;

        try
        {
            Log("startup", null, null, "starting");

            computer = CreateComputer();

            Log("computer", null, null, "created");
            Log("open", null, null, "starting");

            computer.Open();

            Log("open", null, null, "complete");

            List<HardwareDto> hardware = new();

            foreach (IHardware item in computer.Hardware)
            {
                HardwareDto? mapped = ProcessHardware(item, 0);

                if (mapped is not null)
                {
                    hardware.Add(mapped);
                }
            }

            List<SensorDto> allSensors = Flatten(hardware)
                .SelectMany(item => item.Sensors)
                .ToList();

            var payload = new
            {
                provider = "LibreHardwareMonitor",
                providerVersion = typeof(Computer).Assembly
                    .GetName()
                    .Version?
                    .ToString(),
                available = true,
                summary = BuildSummary(allSensors),
                capabilities = BuildCapabilities(allSensors),
                hardware,
                diagnostics = Diagnostics,
                timestamp = DateTimeOffset.UtcNow.ToString("O"),
            };

            Console.WriteLine(JsonSerializer.Serialize(payload, JsonOptions));

            Log("complete", null, null, "success");

            return 0;
        }
        catch (Exception ex)
        {
            Log(
                "fatal",
                null,
                null,
                "failed",
                $"{ex.GetType().Name}: {ex.Message}"
            );

            var payload = new
            {
                provider = "LibreHardwareMonitor",
                available = false,
                error = ex.ToString(),
                diagnostics = Diagnostics,
                hardware = Array.Empty<object>(),
                timestamp = DateTimeOffset.UtcNow.ToString("O"),
            };

            Console.Error.WriteLine(JsonSerializer.Serialize(payload, JsonOptions));

            return 1;
        }
        finally
        {
            if (computer is not null)
            {
                try
                {
                    Log("close", null, null, "starting");
                    computer.Close();
                    Log("close", null, null, "complete");
                }
                catch (Exception ex)
                {
                    Log(
                        "close",
                        null,
                        null,
                        "failed",
                        $"{ex.GetType().Name}: {ex.Message}"
                    );
                }
            }
        }
    }

	private static Computer CreateComputer()
		{
			return new Computer
			{
				IsCpuEnabled = true,
				IsGpuEnabled = true,
				IsMemoryEnabled = true,
				IsMotherboardEnabled = true,
				IsControllerEnabled = true,
				IsNetworkEnabled = true,
				IsStorageEnabled = false,
				IsPowerMonitorEnabled = true,
			};
		}

    private static HardwareDto? ProcessHardware(IHardware hardware, int depth)
    {
        string hardwareType = hardware.HardwareType.ToString();
        string hardwareName = hardware.Name;

        Log("update", hardwareType, hardwareName, "starting");

        try
        {
            hardware.Update();

            Log("update", hardwareType, hardwareName, "complete");
        }
        catch (Exception ex)
        {
            Log(
                "update",
                hardwareType,
                hardwareName,
                "failed",
                $"{ex.GetType().Name}: {ex.Message}"
            );

            return null;
        }

        List<SensorDto> sensors = new();

        Log("sensors", hardwareType, hardwareName, "starting");

        try
        {
            foreach (ISensor sensor in hardware.Sensors)
            {
                sensors.Add(MapSensor(sensor));
            }

            Log("sensors", hardwareType, hardwareName, "complete");
        }
        catch (Exception ex)
        {
            Log(
                "sensors",
                hardwareType,
                hardwareName,
                "failed",
                $"{ex.GetType().Name}: {ex.Message}"
            );
        }

        List<HardwareDto> subHardware = new();

        foreach (IHardware child in hardware.SubHardware)
        {
            HardwareDto? mappedChild = ProcessHardware(child, depth + 1);

            if (mappedChild is not null)
            {
                subHardware.Add(mappedChild);
            }
        }

        return new HardwareDto(
            hardwareName,
            hardwareType,
            hardware.Identifier.ToString(),
            sensors,
            subHardware
        );
    }

    private static SensorDto MapSensor(ISensor sensor)
    {
        return new SensorDto(
            sensor.Name,
            sensor.SensorType.ToString(),
            sensor.Value,
            sensor.Min,
            sensor.Max,
            sensor.Identifier.ToString()
        );
    }

    private static IEnumerable<HardwareDto> Flatten(
        IEnumerable<HardwareDto> items
    )
    {
        foreach (HardwareDto item in items)
        {
            yield return item;

            foreach (HardwareDto child in Flatten(item.SubHardware))
            {
                yield return child;
            }
        }
    }

    private static object BuildCapabilities(
        IReadOnlyCollection<SensorDto> sensors
    )
    {
        bool Has(string type) =>
            sensors.Any(sensor =>
                sensor.Type.Equals(
                    type,
                    StringComparison.OrdinalIgnoreCase
                ) &&
                sensor.Value.HasValue
            );

        return new
        {
            temperature = Has("Temperature"),
            fanSpeed = Has("Fan"),
            voltage = Has("Voltage"),
            power = Has("Power"),
            load = Has("Load"),
            clock = Has("Clock"),
            data = Has("Data"),
            smallData = Has("SmallData"),
            throughput = Has("Throughput"),
            battery = sensors.Any(sensor =>
                sensor.Identifier.Contains(
                    "battery",
                    StringComparison.OrdinalIgnoreCase
                )
            ),
        };
    }

    private static object BuildSummary(
        IReadOnlyList<SensorDto> sensors
    )
    {
        float? Highest(string type, params string[] names)
        {
            List<float> values = sensors
                .Where(sensor =>
                    sensor.Type.Equals(
                        type,
                        StringComparison.OrdinalIgnoreCase
                    ) &&
                    sensor.Value.HasValue &&
                    names.Any(name =>
                        sensor.Name.Contains(
                            name,
                            StringComparison.OrdinalIgnoreCase
                        )
                    )
                )
                .Select(sensor => sensor.Value!.Value)
                .ToList();

            return values.Count == 0 ? null : values.Max();
        }

        float? First(string type, params string[] names)
        {
            return sensors
                .FirstOrDefault(sensor =>
                    sensor.Type.Equals(
                        type,
                        StringComparison.OrdinalIgnoreCase
                    ) &&
                    sensor.Value.HasValue &&
                    names.Any(name =>
                        sensor.Name.Contains(
                            name,
                            StringComparison.OrdinalIgnoreCase
                        )
                    )
                )
                ?.Value;
        }

        List<object> fans = sensors
            .Where(sensor =>
                sensor.Type.Equals(
                    "Fan",
                    StringComparison.OrdinalIgnoreCase
                ) &&
                sensor.Value.HasValue
            )
            .Select(sensor => (object)new
            {
                name = sensor.Name,
                rpm = sensor.Value,
                identifier = sensor.Identifier,
            })
            .ToList();

        List<object> temperatures = sensors
            .Where(sensor =>
                sensor.Type.Equals(
                    "Temperature",
                    StringComparison.OrdinalIgnoreCase
                ) &&
                sensor.Value.HasValue
            )
            .Select(sensor => (object)new
            {
                name = sensor.Name,
                valueC = sensor.Value,
                minC = sensor.Minimum,
                maxC = sensor.Maximum,
                identifier = sensor.Identifier,
            })
            .ToList();

        return new
        {
            cpuTemperatureC = Highest(
                "Temperature",
                "CPU Package",
                "Core Average",
                "CPU Core"
            ),
            cpuPowerW = First(
                "Power",
                "CPU Package",
                "Package"
            ),
            gpuTemperatureC = Highest(
                "Temperature",
                "GPU Core",
                "GPU Hot Spot"
            ),
            gpuLoadPercent = First(
                "Load",
                "GPU Core"
            ),
            gpuMemoryUsedMb = First(
                "SmallData",
                "GPU Memory Used"
            ),
            storageTemperatureC = Highest(
                "Temperature",
                "Drive Temperature",
                "Composite"
            ),
            fans,
            temperatures,
        };
    }

    private static void Log(
        string stage,
        string? hardwareType,
        string? hardwareName,
        string status,
        string? error = null
    )
    {
        Diagnostics.Add(
            new DiagnosticDto(
                stage,
                hardwareType,
                hardwareName,
                status,
                error
            )
        );

        string target = hardwareType is null
            ? string.Empty
            : $" [{hardwareType}] {hardwareName}";

        string errorText = error is null
            ? string.Empty
            : $" | {error}";

        Console.Error.WriteLine(
            $"{DateTimeOffset.Now:HH:mm:ss.fff} " +
            $"{stage}{target}: {status}{errorText}"
        );

        Console.Error.Flush();
    }
}