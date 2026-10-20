using System;
using Cortex.Models;
using Cortex.Services;
using Xunit;

namespace Cortex.Tests;

public class ConfigImportSafetyTests
{
    [Theory]
    [InlineData("""{"FormatVersion":2,"Channels":[]}""")]
    [InlineData("""{"Channels":[{"PWMMin":80,"PWMMax":20}]}""")]
    [InlineData("""{"Channels":[{"ChanType":255}]}""")]
    [InlineData("""{"DigitalInputs":[{"InputNumber":0}]}""")]
    [InlineData("""{"AnalogueInputs":[{"InputNumber":9}]}""")]
    [InlineData("""{"DigitalInputs":[{"InputNumber":2}]}""")]
    [InlineData("""{"AnalogueInputs":[{"InputNumber":1},{"InputNumber":1}]}""")]
    [InlineData("""{"AnalogueInputs":[{"ChanType":255}]}""")]
    [InlineData("""{"AnalogueInputs":[{"Units":255}]}""")]
    public void InvalidImport_IsRejectedWithoutSnapshot(string json)
    {
        Assert.False(ConfigFileSerializer.TryDeserialize(json, out var snapshot, out var error));
        Assert.Null(snapshot);
        Assert.False(string.IsNullOrWhiteSpace(error));
    }

    [Fact]
    public void ApplySnapshot_ValidatesEntireDocumentBeforeChangingAnyChannel()
    {
        var data = new DataStructures();
        data.ChannelsStaticData[0].PWMSetDuty = 12;
        var snapshot = new ConfigSnapshot
        {
            Channels = [new() { PWMSetDuty = 30 }, new() { CurrentThresholdHigh = 18 }]
        };
        Assert.Throws<ArgumentException>(() => ConfigFileSerializer.ApplySnapshot(data, snapshot));
        Assert.Equal(12, data.ChannelsStaticData[0].PWMSetDuty);
    }

    [Fact]
    public void ApplySnapshot_ChecksPartialThresholdAgainstExistingOtherBound()
    {
        var data = new DataStructures();
        data.ChannelsStaticData[0].CurrentThresholdHigh = 10;
        var snapshot = new ConfigSnapshot { Channels = [new() { CurrentThresholdLow = 12 }] };
        Assert.Throws<ArgumentException>(() => ConfigFileSerializer.ApplySnapshot(data, snapshot));
        Assert.Equal(0, data.ChannelsStaticData[0].CurrentThresholdLow);
    }

    [Fact]
    public void ApplySnapshot_RejectsNullEntriesBeforePartialMutation()
    {
        var data = new DataStructures();
        var snapshot = new ConfigSnapshot { Channels = [new() { PWMSetDuty = 30 }, null!] };
        Assert.Throws<ArgumentException>(() => ConfigFileSerializer.ApplySnapshot(data, snapshot));
        Assert.Equal(0, data.ChannelsStaticData[0].PWMSetDuty);
    }

    [Fact]
    public void LegacyPartialDocument_PreservesPositionalInputsAndOtherSettings()
    {
        var data = new DataStructures();
        data.ChannelsStaticData[0].PWMSetDuty = 12;
        Assert.True(ConfigFileSerializer.TryDeserialize("""{"DigitalInputs":[{"IsActiveHigh":false}]}""", out var snapshot, out var error), error);
        ConfigFileSerializer.ApplySnapshot(data, snapshot!);
        Assert.Equal(1, data.DigitalInputsStaticData[0].InputNumber);
        Assert.False(data.DigitalInputsStaticData[0].IsActiveHigh);
        Assert.Equal(12, data.ChannelsStaticData[0].PWMSetDuty);
    }
}
