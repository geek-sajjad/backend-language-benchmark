namespace Bench.Data;

public readonly record struct Sale(int Id, byte Region, byte Category, byte CustomerTier, int AmountCents, int Quantity, short Day);

public static class Sales
{
    const ulong Seed = 0x5EED_BE7C_0001;
    const int Count = 50_000;

    // The test 8 dataset; fields are drawn in declaration order (conformance/reference/reference.py).
    public static Sale[] Build()
    {
        var state = Seed;
        ulong Below(ulong n)
        {
            state += 0x9E37_79B9_7F4A_7C15;
            var z = state;
            z = (z ^ (z >> 30)) * 0xBF58_476D_1CE4_E5B9;
            z = (z ^ (z >> 27)) * 0x94D0_49BB_1331_11EB;
            return (z ^ (z >> 31)) % n;
        }

        var sales = new Sale[Count];
        for (var i = 0; i < sales.Length; i++)
        {
            sales[i] = new Sale(
                Id: i + 1,
                Region: (byte)Below(8),
                Category: (byte)Below(50),
                CustomerTier: (byte)Below(4),
                AmountCents: 100 + (int)Below(100_000),
                Quantity: 1 + (int)Below(10),
                Day: (short)Below(365));
        }
        return sales;
    }
}
