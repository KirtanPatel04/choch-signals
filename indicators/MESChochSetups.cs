#region Using declarations
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Windows.Media;
using System.Xml.Serialization;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
using NinjaTrader.Gui;
using NinjaTrader.Gui.Chart;
using NinjaTrader.NinjaScript;
using NinjaTrader.NinjaScript.DrawingTools;
#endregion

public enum MESChochTrendMode { Structure15M, Vwap, None }

namespace NinjaTrader.NinjaScript.Indicators
{
    /// <summary>
    /// MES CHoCH Setups: key level -> 1M change of character -> limit entry at the 1M fair-value gap.
    /// A+ = strong change-of-character candle (body >= 1.5x the average 1M range), at a 15M swing
    /// level, and NOT at the previous day's high/low: the traits that separated winners from losers
    /// in the mes-backtester tests. Port of backtester/strategies.py (_choch_setups, key_levels, trend).
    /// Use on a 1-minute MES chart with the "CME US Index Futures ETH" trading hours and 5+ days loaded.
    /// The Signal plot is +1 (A+ long), -1 (A+ short) or 0, for Market Analyzer, alerts or a strategy.
    /// </summary>
    public class MESChochSetups : Indicator
    {
        private class Level { public double Price; public int Kind; public DateTime Active; }
        private class Bucket { public DateTime Start; public double High, Low, Close; }

        // kind: 1 = 15M high, 2 = 15M low, 3 = 5M high, 4 = 5M low, 5 = prev day high, 6 = prev day low
        private List<Level> lv15, lv5, lvPD;
        private List<Bucket> done15, done5;
        private Bucket cur15, cur5;
        private int structure;
        private double sh15 = double.NaN, sl15 = double.NaN;
        private double swHi = double.NaN, swLo = double.NaN;
        private DateTime sessDate = DateTime.MinValue;
        private double sessHigh, sessLow, pdh = double.NaN, pdl = double.NaN;
        private double vwPV, vwV;
        private TimeZoneInfo central;
        private Brush bullBack, bearBack;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description = "Key level -> 1M change of character -> entry at the 1M fair-value gap. A+ = strong candle at a 15M level, not at the previous day high/low.";
                Name = "MESChochSetups";
                Calculate = Calculate.OnBarClose;
                IsOverlay = true;
                DrawOnPricePanel = true;
                IsSuspendedWhileInactive = true;
                TrendMode = MESChochTrendMode.Structure15M;
                ShowB = false;
                NearPoints = 1.0;
                SweepBars = 20;
                MinDisplacement = 1.5;
                LookbackDays = 3;
                MinStopPoints = 2.0;
                ShowBoxes = true;
                BoxTargetR = 3.0;
                RunnerR = 8.0;
                BoxMinutes = 30;
                TintBackground = true;
                AlertsOn = true;
                AddPlot(new Stroke(Brushes.Orange, 1), PlotStyle.Hash, "PrevDayHigh");
                AddPlot(new Stroke(Brushes.Orange, 1), PlotStyle.Hash, "PrevDayLow");
                AddPlot(Brushes.Transparent, "Signal");
                AddPlot(Brushes.Transparent, "Entry");
                AddPlot(Brushes.Transparent, "Stop");
            }
            else if (State == State.DataLoaded)
            {
                lv15 = new List<Level>(); lv5 = new List<Level>(); lvPD = new List<Level>();
                done15 = new List<Bucket>(); done5 = new List<Bucket>();
                central = TimeZoneInfo.FindSystemTimeZoneById("Central Standard Time");
                bullBack = new SolidColorBrush(Color.FromArgb(18, 38, 166, 154)); bullBack.Freeze();
                bearBack = new SolidColorBrush(Color.FromArgb(18, 239, 83, 80)); bearBack.Freeze();
            }
        }

        // NinjaTrader stamps bars with their CLOSE time; the backtester uses bar START time in Chicago.
        private DateTime BarStartCT(int barsAgo)
        {
            return TimeZoneInfo.ConvertTime(Time[barsAgo], Core.Globals.GeneralOptions.TimeZoneInfo, central).AddMinutes(-1);
        }

        private static DateTime Floor(DateTime t, int minutes)
        {
            return new DateTime(t.Year, t.Month, t.Day, t.Hour, t.Minute / minutes * minutes, 0);
        }

        private void Finalize(int tf, Bucket b)
        {
            List<Bucket> done = tf == 15 ? done15 : done5;
            done.Add(b);
            if (done.Count > 4) done.RemoveAt(0);
            int n = done.Count;
            if (n >= 3)
            {
                // swing at the middle candle, usable from the end of the newest candle
                Bucket a = done[n - 3], m = done[n - 2], c = done[n - 1];
                DateTime active = c.Start.AddMinutes(tf);
                List<Level> list = tf == 15 ? lv15 : lv5;
                if (m.High > a.High && m.High > c.High) list.Add(new Level { Price = m.High, Kind = tf == 15 ? 1 : 3, Active = active });
                if (m.Low < a.Low && m.Low < c.Low) list.Add(new Level { Price = m.Low, Kind = tf == 15 ? 2 : 4, Active = active });
            }
            if (tf == 15)
            {
                if (n >= 3)
                {
                    Bucket p2 = done[n - 3], p1 = done[n - 2], k = done[n - 1];
                    if (p1.High > p2.High && p1.High >= k.High) sh15 = p1.High;
                    if (p1.Low < p2.Low && p1.Low <= k.Low) sl15 = p1.Low;
                }
                if (!double.IsNaN(sh15) && b.Close > sh15) structure = 1;
                else if (!double.IsNaN(sl15) && b.Close < sl15) structure = -1;
            }
        }

        private void UpdateBucket(int tf, DateTime start)
        {
            DateTime bs = Floor(start, tf);
            Bucket cur = tf == 15 ? cur15 : cur5;
            if (cur != null && cur.Start != bs) { Finalize(tf, cur); cur = null; }
            if (cur == null) cur = new Bucket { Start = bs, High = High[0], Low = Low[0], Close = Close[0] };
            else { cur.High = Math.Max(cur.High, High[0]); cur.Low = Math.Min(cur.Low, Low[0]); cur.Close = Close[0]; }
            if (start.Minute % tf == tf - 1) { Finalize(tf, cur); cur = null; }  // last minute of the candle
            if (tf == 15) cur15 = cur; else cur5 = cur;
        }

        protected override void OnBarUpdate()
        {
            DateTime start = BarStartCT(0);

            // session (17:00 CT open) -> previous day high / low, VWAP
            DateTime sd = start.AddHours(7).Date;
            if (sd != sessDate)
            {
                if (sessDate != DateTime.MinValue)
                {
                    pdh = sessHigh; pdl = sessLow;
                    lvPD.Add(new Level { Price = pdh, Kind = 5, Active = start });
                    lvPD.Add(new Level { Price = pdl, Kind = 6, Active = start });
                }
                sessDate = sd; sessHigh = High[0]; sessLow = Low[0]; vwPV = 0; vwV = 0;
            }
            sessHigh = Math.Max(sessHigh, High[0]); sessLow = Math.Min(sessLow, Low[0]);
            double vol = Volume[0] > 0 ? Volume[0] : 1;
            vwPV += (High[0] + Low[0] + Close[0]) / 3 * vol; vwV += vol;
            double vwap = vwPV / vwV;

            UpdateBucket(15, start);
            UpdateBucket(5, start);
            DateTime cutoff = start.AddDays(-(LookbackDays + 1));
            lv15.RemoveAll(x => x.Active < cutoff); lv5.RemoveAll(x => x.Active < cutoff); lvPD.RemoveAll(x => x.Active < cutoff);

            if (!double.IsNaN(pdh)) { Values[0][0] = pdh; Values[1][0] = pdl; }
            Values[2][0] = 0;

            int trend = TrendMode == MESChochTrendMode.Structure15M ? structure
                      : TrendMode == MESChochTrendMode.Vwap ? Math.Sign(Close[0] - vwap) : 0;
            if (TintBackground && TrendMode != MESChochTrendMode.None)
                BackBrush = trend == 1 ? bullBack : trend == -1 ? bearBack : null;

            if (CurrentBar < 2) return;
            if (High[1] > High[2] && High[1] >= High[0]) swHi = High[1];
            if (Low[1] < Low[2] && Low[1] <= Low[0]) swLo = Low[1];
            if (CurrentBar < Math.Max(SweepBars, 31)) return;

            int side = 0;
            if (!double.IsNaN(swHi) && Close[1] > swHi && Close[1] > Open[1] && Low[0] > High[2]) side = 1;
            else if (!double.IsNaN(swLo) && Close[1] < swLo && Close[1] < Open[1] && High[0] < Low[2]) side = -1;
            if (side == 0) return;
            if (TrendMode != MESChochTrendMode.None && trend * side <= 0) return;

            double avgRng = 0;
            for (int b = 1; b <= 30; b++) avgRng += High[b] - Low[b];
            avgRng /= 30;
            if (avgRng <= 0) return;

            // the push into the level: earliest extreme over the last SweepBars+1 bars
            int k = SweepBars;
            for (int b = SweepBars; b >= 0; b--)
                if (side == 1 ? Low[b] < Low[k] : High[b] > High[k]) k = b;
            double ext = side == 1 ? Low[k] : High[k];
            DateTime extT = BarStartCT(k);
            double extC = Close[k];

            int cnt = 0, keyW = 0;
            double key = double.NaN;
            bool[] kinds = new bool[7];
            foreach (List<Level> list in new[] { lv15, lv5, lvPD })
                foreach (Level x in list)
                {
                    if (x.Active > extT || (extT - x.Active).TotalDays > LookbackDays || Math.Abs(x.Price - ext) > NearPoints) continue;
                    int w = x.Kind <= 2 ? 2 : x.Kind <= 4 ? 1 : 3;
                    cnt++;
                    kinds[x.Kind] = true;
                    if (w > keyW) { keyW = w; key = x.Price; }
                }
            if (cnt == 0) return;

            int nKinds = 0; double strength = 0;
            for (int kd = 1; kd <= 6; kd++)
                if (kinds[kd]) { nKinds++; strength += kd <= 2 ? 2 : kd <= 4 ? 1 : 3; }
            strength += 0.5 * Math.Min(cnt - nKinds, 3);
            bool swept = (ext - key) * -side > 0 && (extC - key) * side > 0;
            double disp = Math.Min(Math.Abs(Close[1] - Open[1]) / avgRng, 3.0);
            double score = strength + (swept ? 1 : 0) + disp;
            bool has15 = kinds[1] || kinds[2], hasPD = kinds[5] || kinds[6];
            bool aPlus = disp >= MinDisplacement && has15 && !hasPD;
            if (!aPlus && !ShowB) return;

            double tick = TickSize;
            double entry = side == 1 ? Math.Floor((Low[0] + High[2]) / 2 / tick) * tick
                                     : Math.Ceiling((High[0] + Low[2]) / 2 / tick) * tick;
            double stopPts = Math.Max(Math.Abs(entry - (ext - side * tick)), MinStopPoints);
            double stop = entry - side * stopPts;
            if (aPlus) { Values[2][0] = side; Values[3][0] = entry; Values[4][0] = stop; }

            string tag = "choch" + CurrentBar;
            string what = (aPlus ? "A+ " : "B ") + (side == 1 ? "LONG" : "SHORT");
            string lvls = (hasPD ? "prev day " : "") + (has15 ? "15M " : "") + (kinds[3] || kinds[4] ? "5M " : "") + "level";
            string text = what + "\nbody " + disp.ToString("0.0") + "x · " + lvls + (swept ? " · swept" : "") + "\nscore " + score.ToString("0.0");
            Brush col = side == 1 ? (aPlus ? Brushes.LimeGreen : Brushes.DarkSeaGreen) : (aPlus ? Brushes.Red : Brushes.IndianRed);
            if (side == 1) Draw.ArrowUp(this, tag + "a", false, 0, Low[0] - 2 * tick, col);
            else Draw.ArrowDown(this, tag + "a", false, 0, High[0] + 2 * tick, col);
            Draw.Text(this, tag + "t", text, 0, side == 1 ? Low[0] - 8 * tick : High[0] + 8 * tick, col);

            if (ShowBoxes)
            {
                DateTime t0 = Time[0], t1 = Time[0].AddMinutes(BoxMinutes);
                double tgt = entry + side * stopPts * BoxTargetR, run = entry + side * stopPts * RunnerR;
                Draw.Rectangle(this, tag + "r", false, t0, entry, t1, stop, Brushes.Red, Brushes.Red, 20);
                Draw.Rectangle(this, tag + "g", false, t0, entry, t1, tgt, Brushes.Teal, Brushes.Teal, 20);
                Draw.Line(this, tag + "e", false, t0, entry, t1, entry, Brushes.White, DashStyleHelper.Solid, 1);
                Draw.Line(this, tag + "x", false, t0, run, t1, run, Brushes.Teal, DashStyleHelper.Dash, 1);
            }

            if (aPlus && AlertsOn && State == State.Realtime)
                Alert("choch" + CurrentBar, Priority.High,
                      what + " MES: limit " + entry.ToString("0.00") + ", stop " + stop.ToString("0.00")
                      + ", 2R " + (entry + side * stopPts * 2).ToString("0.00") + ", score " + score.ToString("0.0"),
                      NinjaTrader.Core.Globals.InstallDir + @"\sounds\Alert1.wav", 10, Brushes.Black, col);
        }

        #region Properties
        [NinjaScriptProperty]
        [Display(Name = "Trend filter", Order = 1, GroupName = "Setup")]
        public MESChochTrendMode TrendMode { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Also show B setups", Order = 2, GroupName = "Setup")]
        public bool ShowB { get; set; }

        [NinjaScriptProperty]
        [Range(0, double.MaxValue)]
        [Display(Name = "Level touch distance (pts)", Order = 3, GroupName = "Setup")]
        public double NearPoints { get; set; }

        [NinjaScriptProperty]
        [Range(2, int.MaxValue)]
        [Display(Name = "Bars back to find the push into a level", Order = 4, GroupName = "Setup")]
        public int SweepBars { get; set; }

        [NinjaScriptProperty]
        [Range(0, double.MaxValue)]
        [Display(Name = "A+ min CHoCH candle body (x avg range)", Order = 5, GroupName = "Setup")]
        public double MinDisplacement { get; set; }

        [NinjaScriptProperty]
        [Range(1, int.MaxValue)]
        [Display(Name = "Level lookback (days)", Order = 6, GroupName = "Setup")]
        public int LookbackDays { get; set; }

        [NinjaScriptProperty]
        [Range(0, double.MaxValue)]
        [Display(Name = "Min stop (pts)", Order = 7, GroupName = "Setup")]
        public double MinStopPoints { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Draw long/short boxes", Order = 1, GroupName = "Drawing")]
        public bool ShowBoxes { get; set; }

        [NinjaScriptProperty]
        [Range(0.5, double.MaxValue)]
        [Display(Name = "Green box target (R)", Order = 2, GroupName = "Drawing")]
        public double BoxTargetR { get; set; }

        [NinjaScriptProperty]
        [Range(1, double.MaxValue)]
        [Display(Name = "Runner cap line (R)", Order = 3, GroupName = "Drawing")]
        public double RunnerR { get; set; }

        [NinjaScriptProperty]
        [Range(5, int.MaxValue)]
        [Display(Name = "Box length (minutes)", Order = 4, GroupName = "Drawing")]
        public int BoxMinutes { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Tint background by trend", Order = 5, GroupName = "Drawing")]
        public bool TintBackground { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Alerts on A+ setups", Order = 6, GroupName = "Drawing")]
        public bool AlertsOn { get; set; }

        [Browsable(false)]
        [XmlIgnore]
        public Series<double> Signal { get { return Values[2]; } }
        #endregion
    }
}
