using System.Text.Json;
using System.Text.Json.Serialization;
using LibreHardwareMonitor.Hardware;

namespace NetworkDashboard.HardwareHelper;

internal sealed class UpdateVisitor : IVisitor
{
    public void VisitComputer(IComputer computer) => computer.Traverse(this);

    public void VisitHardware(IHardware hardware)
    {
        hardware.Update();
        foreach (IHardware subHardware in hardware.SubHardware)
        {
            subHardware.Accept(this);
        }
    }

    public void VisitSensor(ISensor sensor) { }
    public void VisitParameter(IParameter parameter) { }
}

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

internal static class Program
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        WriteIndented = false,
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };

    public static int Main()
    {
        try
        {
            using Computer computer = new()
            {
                IsCpuEnabled = true,
                IsGpuEnabled = true,
                IsMemoryEnabled = true,
                IsMotherboardEnabled = true,
                IsControllerEnabled = true,
                IsNetworkEnabled = true,
                IsStorageEnabled = true,
                IsPowerMonitorEnabled = true,
            };

            computer.Open();
            computer.Accept(new UpdateVisitor());

            List<HardwareDto> hardware = computer.Hardware
                .Select(MapHardware)
                .ToList();

            var allSensors = Flatten(hardware).SelectMany(item => item.Sensors).ToList();

            var payload = new
            {
                provider = "LibreHardwareMonitor",
                providerVersion = typeof(Computer).Assembly.GetName().Version?.ToString(),
                available = true,
                summary = BuildSummary(allSensors),
                hardware,
                timestamp = DateTimeOffset.UtcNow.ToString("O"),
            };

            Console.OutputEncoding = System.Text.Encoding.UTF8;
            Console.WriteLine(JsonSerializer.Serialize(payload, JsonOptions));
            return 0;
        }
        catch (Exception ex)
        {
            var payload = new
            {
                provider = "LibreHardwareMonitor",
                available = false,
                error = ex.ToString(),
                summary = new { },
                hardware = Array.Empty<object>(),
                timestamp = DateTimeOffset.UtcNow.ToString("O"),
            };

            Console.Error.WriteLine(JsonSerializer.Serialize(payload, JsonOptions));
            return 1;
        }
    }

    private static HardwareDto MapHardware(IHardware hardware)
    {
        return new HardwareDto(
            hardware.Name,
            hardware.HardwareType.ToString(),
            hardware.Identifier.ToString(),
            hardware.Sensors.Select(sensor => new SensorDto(
                sensor.Name,
                sensor.SensorType.ToString(),
                sensor.Value,
                sensor.Min,
                sensor.Max,
                sensor.Identifier.ToString()
            )).ToList(),
            hardware.SubHardware.Select(MapHardware).ToList()
        );
    }

    private static IEnumerable<HardwareDto> Flatten(IEnumerable<HardwareDto> items)
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

    private static object BuildSummary(IReadOnlyList<SensorDto> sensors)
    {
        float? Highest(string type, params string[] names) =>
            sensors
                .Where(sensor =>
                    sensor.Type.Equals(type, StringComparison.OrdinalIgnoreCase) &&
                    sensor.Value.HasValue &&
                    names.Any(name =>
                        sensor.Name.Contains(name, StringComparison.OrdinalIgnoreCase)))
                .Select(sensor => sensor.Value)
                .Where(value => value.HasValue)
                .Select(value => value!.Value)
                .DefaultIfEmpty()
                .Max() is float value && value != 0 ? value : null;

        float? First(string type, params string[] names) =>
            sensors.FirstOrDefault(sensor =>
                sensor.Type.Equals(type, StringComparison.OrdinalIgnoreCase) &&
                sensor.Value.HasValue &&
                names.Any(name =>
                    sensor.Name.Contains(name, StringComparison.OrdinalIgnoreCase))
            )?.Value;

        List<object> fans = sensors
            .Where(sensor =>
                sensor.Type.Equals("Fan", StringComparison.OrdinalIgnoreCase) &&
                sensor.Value.HasValue)
            .Select(sensor => (object)new
            {
                name = sensor.Name,
                rpm = sensor.Value,
                identifier = sensor.Identifier,
            })
            .ToList();

        List<object> temperatures = sensors
            .Where(sensor =>
                sensor.Type.Equals("Temperature", StringComparison.OrdinalIgnoreCase) &&
                sensor.Value.HasValue)
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
            cpuTemperatureC = Highest("Temperature", "CPU Package", "Core Average", "CPU Core"),
            cpuPowerW = First("Power", "CPU Package", "Package"),
            gpuTemperatureC = Highest("Temperature", "GPU Core", "GPU Hot Spot"),
            gpuLoadPercent = First("Load", "GPU Core"),
            gpuMemoryUsedMb = First("SmallData", "GPU Memory Used"),
            storageTemperatureC = Highest("Temperature", "Temperature"),
            fans,
            temperatures,
        };
    }
}
